import os
from copy import deepcopy

import numpy as np
import torch
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.multiprocessing as mp
import torch.nn as nn
from torch.cuda.amp import GradScaler, autocast
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.tensorboard import SummaryWriter

from ..functional import FUNCTIONAL_REGISTRY
from ..utils import AverageMeter, endpointerror
from .registry import loss_functions, optimizers, schedulers


def seed(seed):

    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    cudnn.benchmark = False
    cudnn.deterministic = True


class BaseTrainer:
    def __init__(self):
        self.cfg = None

        self.model = None

        self.loss_fn = None
        self.optimizer = None
        self.scheduler = None
        self.scaler = None

        self.train_loader = None
        self.val_loader = None

        self.device = None
        self._trainer = None

        self.model_parallel = False

        self.writer = None

    def _setup_device(self):
        pass

    def _setup_model(self):
        pass

    def _setup_training(self, loss_fn=None, optimizer=None, scheduler=None):
        if loss_fn is None:

            if self.cfg.CRITERION.CUSTOM:
                loss = FUNCTIONAL_REGISTRY.get(self.cfg.CRITERION.NAME)
            else:
                loss = loss_functions.get(self.cfg.CRITERION.NAME)

            if self.cfg.CRITERION.PARAMS is not None:
                loss_params = self.cfg.CRITERION.PARAMS.to_dict()
                loss_fn = loss(**loss_params)
            else:
                loss_fn = loss()

        if optimizer is None:

            opt = optimizers.get(self.cfg.OPTIMIZER.NAME)

            if self.cfg.OPTIMIZER.PARAMS is not None:
                optimizer_params = self.cfg.OPTIMIZER.PARAMS.to_dict()
                optimizer = opt(
                    self.model.parameters(),
                    lr=self.cfg.OPTIMIZER.LR,
                    **optimizer_params,
                )
            else:
                optimizer = opt(self.model.parameters(), lr=self.cfg.OPTIMIZER.LR)

        if scheduler is None:

            if self.cfg.SCHEDULER.USE:
                sched = schedulers.get(self.cfg.SCHEDULER.NAME)

                if self.cfg.SCHEDULER.PARAMS is not None:
                    scheduler_params = self.cfg.SCHEDULER.PARAMS.to_dict()
                    scheduler = sched(optimizer, **scheduler_params)
                else:
                    scheduler = sched(optimizer)

        self.loss_fn = loss_fn
        self.optimizer = optimizer
        self.scheduler = scheduler

        self.writer = SummaryWriter(log_dir=self.cfg.LOG_DIR)

        self.scaler = GradScaler(enabled=self.cfg.MIXED_PRECISION)

        self._trainer = self._epoch_trainer
        if self.cfg.num_steps is not None:
            self._trainer = self._step_trainer

        self.min_avg_val_loss = float("inf")
        self.min_avg_val_metric = float("inf")

    def _main_worker(self):
        pass

    def _epoch_trainer(self):

        best_model = deepcopy(self.model)
        self.model.train()

        loss_meter = AverageMeter()

        n_epochs = self.cfg.EPOCHS
        if start_epoch is not None:
            print(f"Resuming training from epoch {start_epoch+1}\n")
        else:
            start_epoch = 0

        for epoch in range(start_epoch, start_epoch + n_epochs):

            print(f"Epoch {epoch+1} of {start_epoch+n_epochs}")
            print("-" * 80)

            loss_meter.reset()
            for iteration, (inp, target) in enumerate(self.train_loader):

                loss = self._run_step(inp, target)

                loss_meter.update(loss.item())

                total_iters = iteration + (epoch * len(self.train_loader))
                self._log_step(iteration, total_iters, loss_meter)

            print(f"\nEpoch {epoch+1}: Training loss = {loss_meter.sum}")
            self.writer.add_scalar("epochs_training_loss", loss_meter.sum, epoch + 1)

            if epoch % self.cfg.VALIDATE_INTERVAL == 0 and self.device == 0:
                new_avg_val_loss, new_avg_val_metric = self._validate_model(epoch)

                self.writer.add_scalar(
                    "avg_validation_loss", new_avg_val_loss, epoch + 1
                )
                print(f"Epoch {epoch+1}: Average validation loss = {new_avg_val_loss}")

                self.writer.add_scalar(
                    "avg_validation_metric", new_avg_val_metric, epoch + 1
                )
                print(
                    f"Epoch {epoch+1}: Average validation metric = {new_avg_val_metric}"
                )

                best_model = self._save_best_model(new_avg_val_loss, new_avg_val_metric)

            if epoch % self.cfg.CKPT_INTERVAL == 0 and self.device == 0:
                self._save_checkpoints()

        writer.close()
        return best_model

    def _step_trainer(self):
        pass

    def _run_step(self, inp, target):
        img1, img2 = inp
        img1, img2, target = (
            img1.to(self.device),
            img2.to(self.device),
            target.to(self.device),
        )
        target = target / self.cfg.DATA.TARGET_SCALE_FACTOR

        with autocast(enabled=self.cfg.MIXED_PRECISION):
            pred = self.model(img1, img2)

            loss = self.loss_fn(pred, target)

        self.optimizer.zero_grad()

        self.scaler.scale(loss).backward()
        self.scaler.unscale_(self.optimizer)

        if self.cfg.GRAD_CLIP.USE is True:
            nn.utils.clip_grad_norm_(self.model.parameters(), self.cfg.GRAD_CLIP.VALUE)

        self.scaler.step(self.optimizer)

        if self.scheduler is not None:
            self.scheduler.step()

        self.scaler.update()

        return loss

    def _log_step(self, iteration, total_iters, loss_meter):
        if iteration % self.cfg.LOG_ITERATIONS_INTERVAL == 0:

            self.writer.add_scalar(
                "avg_batch_training_loss",
                loss_meter.avg,
                total_iters,
            )
            print(
                f"Epoch iterations: {iteration}, Total iterations: {total_iters}, Average batch training loss: {loss_meter.avg}"
            )

    def _validate_model(self, total_iters):

        self.model.eval()

        metric_meter = AverageMeter()
        loss_meter = AverageMeter()

        with torch.no_grad():
            for inp, target in self.val_loader:

                img1, img2 = inp
                img1, img2, target = (
                    img1.to(self.device),
                    img2.to(self.device),
                    target.to(self.device),
                )
                target = target / self.cfg.DATA.TARGET_SCALE_FACTOR

                pred = self.model(img1, img2)

                loss = self.loss_fn(pred, target)
                loss_meter.update(loss.item())

                metric = self._calculate_metric(pred, target)
                metric_meter.update(metric)

        self.model.train()

        return loss_meter.avg, metric_meter.avg

    def _calculate_metric(self, pred, target):
        return endpointerror(pred, target)

    def _save_checkpoints(self):
        if self.model_parallel:
            save_model = self.model.module
        else:
            save_model = self.model

        consolidated_save_dict = {
            "model_state_dict": save_model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "epochs": self.epochs,
        }
        if scheduler is not None:
            consolidated_save_dict["scheduler_state_dict"] = self.scheduler.state_dict()

        torch.save(
            consolidated_save_dict,
            os.path.join(
                self.cfg.CKPT_DIR,
                self.model_name + "_epochs" + str(epochs + 1) + ".pth",
            ),
        )

    def _save_best_model(self, new_avg_val_loss, new_avg_val_metric):
        if new_avg_val_loss < self.min_avg_val_loss:

            self.min_avg_val_loss = new_avg_val_loss
            print("New minimum average validation loss!")

            if self.cfg.VALIDATE_ON.lower() == "loss":
                best_model = deepcopy(self.model)
                save_best_model = (
                    best_model.module if self.model_parallel else best_model
                )
                torch.save(
                    save_best_model.state_dict(),
                    os.path.join(self.cfg.CKPT_DIR, self.model_name + "_best.pth"),
                )
                print(f"Saved new best model!")

            return best_model

        if new_avg_val_metric < self.min_avg_val_metric:

            self.min_avg_val_metric = new_avg_val_metric
            print("New minimum average validation metric!")

            if self.cfg.VALIDATE_ON.lower() == "metric":
                best_model = deepcopy(self.model)
                save_best_model = (
                    best_model.module if self.model_parallel else best_model
                )
                torch.save(
                    save_best_model.state_dict(),
                    os.path.join(self.cfg.CKPT_DIR, self.model_name + "_best.pth"),
                )
                print(f"Saved new best model!")

            return best_model

    def _reload_trainer_state(self):
        pass


class Trainer(BaseTrainer):
    """
    Trainer class for training and evaluating models
    on a single device CPU/GPU.

    Parameters
    ----------

    cfg : CfgNode
        Configuration object for training
    model : torch.nn.Module
        Model to be trained
    train_loader : torch.utils.data.DataLoader
        DataLoader for training
    val_loader : torch.utils.data.DataLoader
        DataLoader for validation
    """

    def __init__(self, cfg, model, train_loader, val_loader):
        self.cfg = cfg
        self.model_name = model.__class__.__name__.lower()
        self._setup_model(model)

        self.train_loader = train_loader
        self.val_loader = val_loader

    def _setup_device(self):
        assert (
            len(self.cfg.DEVICE) == 1
        ), "Multiple devices not supported. Use ezflow.DistributedTrainer for multi-gpu training."

        if int(self.cfg.DEVICE) == -1 or self.cfg.DEVICE == "cpu":
            self.device = torch.device("cpu")
            self.cfg.MIXED_PRECISION = False
            print("Running on CPU\n")

        elif not torch.cuda.is_available():
            self.device = torch.device("cpu")
            self.cfg.MIXED_PRECISION = False
            print("CUDA device(s) not available. Running on CPU\n")

        else:
            self.device = torch.device(int(self.cfg.DEVICE))

    def _setup_model(self):
        self.model = self.model.to(self.device)

    def _main_worker(
        self, loss_fn=None, optimizer=None, scheduler=None, start_epoch=None
    ):
        self._setup_device()
        self._setup_model()
        self._setup_training()

        os.makedirs(self.cfg.CKPT_DIR, exist_ok=True)
        os.makedirs(self.cfg.LOG_DIR, exist_ok=True)

        best_model = self._trainer()

    def train(self):
        pass


class DistributedTrainer(BaseTrainer):
    """
    Trainer class for distributed training and evaluating models
    on a single node multi-gpu environment.

    Parameters
    ----------

    cfg : CfgNode
        Configuration object for training
    model : torch.nn.Module
        Model to be trained
    train_loader_creator : ezflow.DataloaderCreator
        DataloaderCreator instance for training
    val_loader_creator : ezflow.DataloaderCreator
        DataloaderCreator instance for validation
    """

    def __init__(self):

        self._validate_ddp_config()

    def _validate_ddp_config(self):
        if self.cfg.DISTRIBUTED.USE:

            if self.cfg.DEVICE != "all":
                """
                Set CUDA_VISIBLE_DEVICES before performing any torch.cuda operations.
                """

                device = self.cfg.DEVICE
                if type(device) != str:
                    device = str(device)

                os.environ["CUDA_VISIBLE_DEVICES"] = device

                device_ids = device.split(",")
                device_ids = [int(id) for id in device_ids]
                assert (
                    len(device_ids) <= torch.cuda.device_count()
                ), "Total devices cannot be greater than available CUDA devices."

    def _setup_device(self, rank=0):
        assert (
            torch.cuda.is_available()
        ), "CUDA devices not available. Use ezflow.Trainer for single device training."
        self.device = torch.device(rank)

    def _setup_ddp(self):
        os.environ["MASTER_ADDR"] = self.cfg.DISTRIBUTED.MASTER_ADDR
        os.environ["MASTER_PORT"] = self.cfg.DISTRIBUTED.MASTER_PORT

        seed(0)
        rank = self.device

        dist.init_process_group(
            backend=self.cfg.DISTRIBUTED.BACKEND,
            init_method="env://",
            world_size=self.cfg.DISTRIBUTED.WORLD_SIZE,
            rank=rank,
        )
        print(f"{rank + 1}/{self.cfg.DISTRIBUTED.WORLD_SIZE} process initialized.")

    def _setup_model(self):

        self.model = DDP(
            self.model.cuda(self.device),
            device_ids=[self.device],
        )

        if self.cfg.DISTRIBUTED.SYNC_BATCH_NORM:
            self.model = nn.SyncBatchNorm.convert_sync_batchnorm(self.model)

        self.model = self.model.to(self.device)

    def _cleanup(self):
        dist.destroy_process_group()

import time

import torch
from torch.nn import DataParallel
from torch.nn.parallel import DistributedDataParallel as DDP

from ..utils import AverageMeter, Profiler
from .metrics import endpointerror


def run_inference(model, dataloader, device, metric_fn):
    metric_meter = AverageMeter()
    times = []

    with torch.no_grad():

        for inp, target in dataloader:
            start_time = time.time()

            img1, img2 = inp
            img1, img2, target = (
                img1.to(device),
                img2.to(device),
                target.to(device),
            )

            pred = model(img1, img2)

            if torch.cuda.is_available():
                torch.cuda.synchronize()

            end_time = time.time()
            times.append(end_time - start_time)

            metric = metric_fn(pred, target)
            metric_meter.update(metric.item())

    print(f"Average inference time: {sum(times)/epochs}")
    return metric_meter


def warmup(model, dataloader, device):
    inp, target = iter(dataloader).next()

    img1, img2 = inp
    img1, img2, target = (
        img1.to(device),
        img2.to(device),
        target.to(device),
    )

    model(img1, img2)


def eval_model(
    model, dataloader, device, distributed=False, metric=None, profiler=None
):

    if isinstance(device, list) or isinstance(device, tuple):
        device = ",".join(map(str, device))

    if device == "-1" or device == -1 or device == "cpu":
        device = torch.device("cpu")
        print("Running on CPU\n")

    elif not torch.cuda.is_available():
        device = torch.device("cpu")
        print("CUDA device(s) not available. Running on CPU\n")

    else:
        if device == "all":
            device = torch.device("cuda")
            if distributed:
                model = DDP(model)
            else:
                model = DataParallel(model)
            print(f"Running on all available CUDA devices\n")

        else:
            if type(device) != str:
                device = str(device)

            device_ids = device.split(",")
            device_ids = [int(id) for id in device_ids]
            cuda_str = "cuda:" + device
            device = torch.device(cuda_str)
            if distributed:
                model = DDP(model)
            else:
                model = DataParallel(model, device_ids=device_ids)
            print(f"Running on CUDA devices {device_ids}\n")

    model = model.to(device)
    model.eval()

    metric_fn = metric or endpointerror

    warmup(model, dataloader, device)
    if torch.cuda.is_available():
        torch.cuda.synchronize()

    if profiler is None:
        metric_meter = run_inference(model, dataloader, device, metric_fn)
    else:
        with profile(
            activities=profiler.activites,
            record_shapes=profiler.record_shapes,
            profile_memory=profiler.profile_memory,
            schedule=profiler.schedule,
            on_trace_ready=profiler.on_trace_ready,
        ) as prof:

            metric_meter = run_inference(model, dataloader, device, metric_fn)

            prof.step()

        print(
            prof.key_averages(group_by_input_shape=True).table(
                sort_by="self_cpu_memory_usage", row_limit=10
            )
        )
        print(
            prof.key_averages(group_by_input_shape=True).table(
                sort_by="cpu_memory_usage", row_limit=10
            )
        )
        print(
            prof.key_averages(group_by_input_shape=True).table(
                sort_by="self_cuda_memory_usage", row_limit=10
            )
        )
        print(
            prof.key_averages(group_by_input_shape=True).table(
                sort_by="cuda_memory_usage", row_limit=10
            )
        )

    print(f"Average evaluation metric = {metric_meter.avg}")

    return metric_meter.avg
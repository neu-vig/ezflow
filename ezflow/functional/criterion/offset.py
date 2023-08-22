import torch
import torch.nn as nn
import torch.nn.functional as F

from ...config import configurable
from ..registry import FUNCTIONAL_REGISTRY
from .sequence import SequenceLoss


@FUNCTIONAL_REGISTRY.register()
class OffsetCrossEntropyLoss(nn.Module):
    @configurable
    def __init__(
        self,
        stride=8,
        offset_loss_weight=[0, 1],
        weight_anneal_fn="CosineAnnealer",
        **kwargs,
    ):
        super(OffsetCrossEntropyLoss, self).__init__()
        assert (
            weight_anneal_fn in FUNCTIONAL_REGISTRY
        ), f"{weight_anneal_fn} not found. Available weight annelers {FUNCTIONAL_REGISTRY.get_list()}"

        self.stride = stride

        weight_anneal_fn = FUNCTIONAL_REGISTRY.get(weight_anneal_fn)
        self.weight_annealers = [
            weight_anneal_fn(init_weight=wt, **kwargs) for wt in offset_loss_weight
        ]

    def __compute_loss(self, flow_logits, offset_labs, valid, curr_iter):
        # exlude invalid pixels and extremely large diplacements()
        valid[:, :: self.stride, :: self.stride]
        valid = valid >= 0.5

        logprobs = F.log_softmax(flow_logits, dim=1)
        loss = -(offset_labs * logprobs).sum(dim=1)

        loss = (valid[:, None] * loss).mean()
        return loss

    def forward(self, flow_logits_list, offset_labs, valid, curr_iter):
        logit_loss = 0.0
        for i, flow_logits in enumerate(flow_logits_list):
            loss = self.__compute_loss(flow_logits, offset_labs, valid)
            logit_loss += (self.weight_annealers[i](curr_iter)) * loss

        return loss


@FUNCTIONAL_REGISTRY.register()
class FlowOffsetLoss(nn.Module):
    @configurable
    def __init__(
        self,
        gamma=0.25,
        max_flow=400,
        stride=8,
        offset_loss_weight=[0, 1],
        weight_anneal_fn="CosineAnnealer",
        **kwargs,
    ):
        super(FlowOffsetLoss, self).__init__()

        self.l1_loss = SequenceLoss(gamma=gamma, max_flow=max_flow)
        self.cross_entropy_loss = OffsetCrossEntropyLoss(
            offset_loss_weight=offset_loss_weight,
            weight_anneal_fn=weight_anneal_fn,
            stride=stride,
            **kwargs,
        )

    def forward(self, pred, target, curr_iter):
        assert (
            target.shape[1] == 3
        ), "Incorrect channel dimension. Set append valid mask to True in DataloaderCreator to append the valid data mask in the target label."

        _, valid = target[:, :2, :, :], target[:, 2:, :, :]
        valid = torch.squeeze(valid, dim=1)

        flow_loss = self.l1_loss(pred, target)
        logit_loss = self.cross_entropy_loss(pred, _, valid, curr_iter)
        loss = flow_loss + logit_loss
        return loss
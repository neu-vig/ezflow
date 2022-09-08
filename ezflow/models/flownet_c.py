import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.init import constant_, kaiming_normal_

from ..decoder import build_decoder
from ..encoder import BasicConvEncoder, build_encoder
from ..modules import BaseModule, conv
from ..similarity import IterSpatialCorrelationSampler as SpatialCorrelationSampler
from .build import MODEL_REGISTRY


@MODEL_REGISTRY.register()
class FlowNetC(BaseModule):
    """
    Implementation of **FlowNetCorrelation** from the paper
    `FlowNet: Learning Optical Flow with Convolutional Networks <https://arxiv.org/abs/1504.06852>`_

    Parameters
    ----------
    cfg : :class:`CfgNode`
        Configuration for the model
    """

    def __init__(self, cfg):
        super(FlowNetC, self).__init__()

        self.cfg = cfg

        channels = cfg.ENCODER.CONFIG
        cfg.ENCODER.CONFIG = channels[:3]

        self.feature_encoder = build_encoder(cfg.ENCODER)

        self.correlation_layer = SpatialCorrelationSampler(
            kernel_size=1,
            patch_size=2 * cfg.SIMILARITY.MAX_DISPLACEMENT + 1,
            padding=cfg.SIMILARITY.PAD_SIZE,
            dilation_patch=2,
        )
        self.correlation_layer = CorrelationLayer(
            pad_size=cfg.SIMILARITY.PAD_SIZE,
            max_displacement=cfg.SIMILARITY.MAX_DISPLACEMENT,
        )
        self.corr_activation = nn.LeakyReLU(negative_slope=0.1, inplace=True)

        self.conv_redirect = conv(
            in_channels=cfg.ENCODER.CONFIG[-1], out_channels=32, norm=cfg.ENCODER.NORM
        )

        self.corr_encoder = BasicConvEncoder(
            in_channels=473, config=channels[3:], norm=cfg.ENCODER.NORM
        )

        self.decoder = build_decoder(cfg.DECODER)

        for m in self.modules():
            if isinstance(m, nn.Conv2d) or isinstance(m, nn.ConvTranspose2d):
                kaiming_normal_(m.weight, 0.1)
                if m.bias is not None:
                    constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                constant_(m.weight, 1)
                constant_(m.bias, 0)

    def forward(self, img1, img2):
        """
        Performs forward pass of the network

        Parameters
        ----------
        img1 : torch.Tensor
            Image to predict flow from
        img2 : torch.Tensor
            Image to predict flow to

        Returns
        -------
        torch.Tensor
            Flow from img1 to img2
        """

        H, W = img1.shape[-2:]

        conv_outputs1 = self.feature_encoder(img1)
        conv_outputs2 = self.feature_encoder(img2)

        corr_output = self.correlation_layer(conv_outputs1[-1], conv_outputs2[-1])
        corr_output = self.corr_activation(corr_output)

        # Redirect final feature output of img1
        conv_redirect_output = self.conv_redirect(conv_outputs1[-1])

        x = torch.cat([conv_redirect_output, corr_output], dim=1)

        conv_outputs = self.corr_encoder(x)

        # Add first two convolution output from img1
        conv_outputs = [conv_outputs1[0], conv_outputs1[1]] + conv_outputs

        flow_preds = self.decoder(conv_outputs)
        flow_preds.reverse()

        output = {"flow_preds": flow_preds}

        if self.training:
            return output

        flow = flow_preds[0]

        H_, W_ = flow.shape[-2:]
        flow = F.interpolate(flow, img1.shape[-2:], mode="bilinear", align_corners=True)
        flow_u = flow[:, 0, :, :] * (W / W_)
        flow_v = flow[:, 1, :, :] * (H / H_)
        flow = torch.stack([flow_u, flow_v], dim=1)

        output["flow_upsampled"] = flow
        return output

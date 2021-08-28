import torch
import torch.nn as nn

from ..models import BasicBlock, BottleneckBlock


class BasicEncoder(nn.Module):

    """
    ResNet style encoder using basic residual blocls
    """

    def __init__(
        self,
        in_channels=3,
        out_channels=128,
        norm="batch",
        p_dropout=0.0,
        layer_config=(64, 96, 128),
    ):
        super(BasicEncoder, self).__init__()

        assert norm in ("group", "batch", "instance", "none")

        start_channels = layer_config[0]

        if norm == "group":
            norm_fn = nn.GroupNorm(num_groups=8, num_channels=start_channels)

        elif norm == "batch":
            norm_fn = nn.BatchNorm2d(start_channels)

        elif norm == "instance":
            norm_fn = nn.InstanceNorm2d(start_channels)

        elif norm == "none":
            norm_fn = nn.Sequential()

        layers = [
            nn.Conv2d(in_channels, start_channels, kernel_size=7, stride=2, padding=3),
            norm_fn,
            nn.ReLU(inplace=True),
        ]

        for i in range(len(layer_config)):
            if i == 0:
                stride = 1
            else:
                stride = 2

            layers += self._make_layer(start_channels, layer_config[i], stride, norm)
            start_channels = layer_config[i]

        layers.append(nn.Conv2d(layer_config[-1], out_channels, kernel_size=1))

        dropout = nn.Sequential()
        if self.training and p_dropout > 0:
            dropout = nn.Dropout2d(p=p_dropout)
        layers.append(dropout)

        self.encoder = nn.Sequential(*layers)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            elif isinstance(m, (nn.BatchNorm2d, nn.InstanceNorm2d, nn.GroupNorm)):
                if m.weight is not None:
                    nn.init.constant_(m.weight, 1)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def _make_layer(self, in_channels, out_channels, stride, norm):

        layer1 = BasicBlock(in_channels, out_channels, stride, norm)
        layer2 = BasicBlock(out_channels, out_channels, stride=1, norm=norm)

        return [layer1, layer2]

    def forward(self, x):

        is_list = isinstance(x, tuple) or isinstance(x, list)
        if is_list:
            batch_dim = x[0].shape[0]
            x = torch.cat(x, dim=0)

        out = self.encoder(x)

        if is_list:
            out = torch.split(out, [batch_dim, batch_dim], dim=0)

        return out


class BottleneckEncoder(nn.Module):

    """
    ResNet style encoder using bottleneck residual blocls
    """

    def __init__(
        self,
        in_channels=3,
        out_channels=128,
        norm="batch",
        p_dropout=0.0,
        layer_config=(32, 64, 96),
    ):
        super(BottleneckEncoder, self).__init__()

        assert norm in ("group", "batch", "instance", "none")

        start_channels = layer_config[0]

        if norm == "group":
            norm_fn = nn.GroupNorm(num_groups=8, num_channels=start_channels)

        elif norm == "batch":
            norm_fn = nn.BatchNorm2d(start_channels)

        elif norm == "instance":
            norm_fn = nn.InstanceNorm2d(start_channels)

        elif norm == "none":
            norm_fn = nn.Sequential()

        layers = [
            nn.Conv2d(in_channels, start_channels, kernel_size=7, stride=2, padding=3),
            norm_fn,
            nn.ReLU(inplace=True),
        ]

        for i in range(len(layer_config)):
            if i == 0:
                stride = 1
            else:
                stride = 2

            layers += self._make_layer(start_channels, layer_config[i], stride, norm)
            start_channels = layer_config[i]

        layers.append(nn.Conv2d(layer_config[-1], out_channels, kernel_size=1))

        dropout = nn.Sequential()
        if self.training and p_dropout > 0:
            dropout = nn.Dropout2d(p=p_dropout)
        layers.append(dropout)

        self.encoder = nn.Sequential(*layers)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            elif isinstance(m, (nn.BatchNorm2d, nn.InstanceNorm2d, nn.GroupNorm)):
                if m.weight is not None:
                    nn.init.constant_(m.weight, 1)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def _make_layer(self, in_channels, out_channels, stride, norm):

        layer1 = BottleneckBlock(in_channels, out_channels, stride, norm)
        layer2 = BottleneckBlock(out_channels, out_channels, stride=1, norm=norm)

        return [layer1, layer2]

    def forward(self, x):

        is_list = isinstance(x, tuple) or isinstance(x, list)
        if is_list:
            batch_dim = x[0].shape[0]
            x = torch.cat(x, dim=0)

        out = self.encoder(x)

        if is_list:
            out = torch.split(out, [batch_dim, batch_dim], dim=0)

        return out
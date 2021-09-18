import random

import numpy as np
import torch
import torch.utils.data as data

from ...functional import FlowAugmentor
from ...utils import frame_utils


class BaseDataset(data.Dataset):
    """
    Base dataset for reading synthetic optical flow data.

    Parameters
    ----------
    is_test : bool
        Whether to the dataset is a test set
    init_seed : bool
        Whether to initialize the random seed
    augment : bool
        Whether to perform data augmentation
    crop_size : :obj:`tuple` of :obj:`int`
        The size of the image crop
    aug_params : :obj:`dict`
        The parameters for data augmentation

    """

    def __init__(
        self,
        is_test=False,
        init_seed=False,
        augment=True,
        crop_size=(224, 224),
        aug_params={
            "color_aug_params": {"aug_prob": 0.2},
            "eraser_aug_params": {"aug_prob": 0.5},
            "spatial_aug_params": {"aug_prob": 0.8},
        },
    ):

        self.is_test = is_test
        self.init_seed = init_seed

        self.augmentor = None
        if augment:
            self.augmentor = FlowAugmentor(crop_size, **aug_params)

        self.flow_list = []
        self.image_list = []

    def __getitem__(self, index):
        """
        Returns the corresponding images and the flow between them.

        """
        if self.is_test:
            img1 = frame_utils.read_gen(self.image_list[index][0])
            img2 = frame_utils.read_gen(self.image_list[index][1])
            img1 = np.array(img1).astype(np.uint8)[..., :3]
            img2 = np.array(img2).astype(np.uint8)[..., :3]
            img1 = torch.from_numpy(img1).permute(2, 0, 1).float()
            img2 = torch.from_numpy(img2).permute(2, 0, 1).float()

            return img1, img2

        if not self.init_seed:
            worker_info = torch.utils.data.get_worker_info()
            if worker_info is not None:
                torch.manual_seed(worker_info.id)
                np.random.seed(worker_info.id)
                random.seed(worker_info.id)
                self.init_seed = True

        index = index % len(self.image_list)

        img1 = frame_utils.read_gen(self.image_list[index][0])
        img2 = frame_utils.read_gen(self.image_list[index][1])
        flow = frame_utils.read_gen(self.flow_list[index])

        flow = np.array(flow).astype(np.float32)
        img1 = np.array(img1).astype(np.uint8)
        img2 = np.array(img2).astype(np.uint8)

        # grayscale images
        if len(img1.shape) == 2:
            img1 = np.tile(img1[..., None], (1, 1, 3))
            img2 = np.tile(img2[..., None], (1, 1, 3))
        else:
            img1 = img1[..., :3]
            img2 = img2[..., :3]

        if self.augmentor is not None:
            img1, img2, flow = self.augmentor(img1, img2, flow)

        return img1, img2, flow

    def __rmul__(self, v):
        """
        Returns an instance of the dataset after multiplying with v.

        """
        self.flow_list = v * self.flow_list
        self.image_list = v * self.image_list
        return self

    def __len__(self):
        """
        Return length of the dataset.

        """
        return len(self.image_list)
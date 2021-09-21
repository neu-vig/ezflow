class DeviceDataLoader:
    """
    A data loader wrapper to move data to a specific compute device.
    It can be used to wrap the existing pytorch data loader.

    Parameters
    ----------
    data_loader : torch.utils.data.dataloader
        Whether to the dataset is a test set
    device : torch.device
        the compute device
    """

    def __init__(self, data_loader, device):
        self.data_loader = data_loader
        self.device = device

    def __iter__(self):
        """Yield a batch of data after moving it to a device."""
        for batch in self.data_loader:
            yield batch.to(self.device, non_blocking=True)

    def __len__(self):
        """Return the number of batches."""
        return len(self.data_loader)

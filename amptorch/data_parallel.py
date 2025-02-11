"""
Adapted from https://github.com/Open-Catalyst-Project/ocp/blob/master/ocpmodels/common/data_parallel.py
"""
from itertools import chain
import torch


class DataParallel(torch.nn.DataParallel):
    def __init__(self, module, output_device, num_gpus):
        if num_gpus < 0:
            raise ValueError("# GPUs must be positive.")
        if num_gpus > torch.cuda.device_count():
            raise ValueError("# GPUs specified larger than available")

        if num_gpus == 1:
            device_ids = [output_device]
        else:
            if output_device >= num_gpus:
                raise ValueError("Main device must be less than # of GPUs")
            device_ids = list(range(num_gpus))

        super(DataParallel, self).__init__(
            module=module, device_ids=device_ids, output_device=output_device
        )

        self.src_device = torch.device(output_device)

    def forward(self, batch_list):
        if len(self.device_ids) == 1:
            return self.module(batch_list[0].to(f"cuda:{self.device_ids[0]}"))

        for t in chain(self.module.parameters(), self.module.buffers()):
            if t.device != self.src_device:
                raise RuntimeError(
                    (
                        "Module must have its parameters and buffers on device "
                        "{} but found one of them on device {}."
                    ).format(self.src_device, t.device)
                )

        inputs = [
            batch.to(f"cuda:{self.device_ids[i]}") for i, batch in enumerate(batch_list)
        ]
        replicas = self.replicate(self.module, self.device_ids[: len(inputs)])
        outputs = self.parallel_apply(replicas, inputs, None)
        return self.gather(outputs, self.output_device)


class ParallelCollater:
    def __init__(self, num_gpus, collater):
        self.num_gpus = num_gpus
        self.collater = collater

    def __call__(self, data_list):
        if self.num_gpus <= 1:
            batch = self.collater(data_list)
            batch_list = [batch[0]]
            target_list = [batch[1]]
            return [batch_list, target_list]

        else:
            num_devices = min(self.num_gpus, len(data_list))

            count = torch.tensor([data.num_nodes for data in data_list])
            cumsum = count.cumsum(0)
            cumsum = torch.cat([cumsum.new_zeros(1), cumsum], dim=0)
            device_id = num_devices * cumsum.to(torch.float) / cumsum[-1].item()
            device_id = (device_id[:-1] + device_id[1:]) / 2.0
            device_id = device_id.to(torch.long)
            split = device_id.bincount().cumsum(0)
            split = torch.cat([split.new_zeros(1), split], dim=0)
            split = torch.unique(split, sorted=True)
            split = split.tolist()

            skorch_list = [
                self.collater(data_list[split[i] : split[i + 1]])
                for i in range(len(split) - 1)
            ]
            batch_list = [batch[0] for batch in skorch_list]
            target_list = [batch[1] for batch in skorch_list]
            return [batch_list, target_list]

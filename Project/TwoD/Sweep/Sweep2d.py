import os
import h5py
import numpy as np
import wandb
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from sklearn.model_selection import train_test_split

# 定义模型类 CNNAE
class CNNAE(nn.Module):
    def __init__(self, cnum):
        super(CNNAE, self).__init__()
        # 编码器
        self.cnum = cnum
        self.encoder = nn.Sequential(
            nn.Conv2d(3, cnum, 3, stride=2, padding=1),
            nn.BatchNorm2d(cnum),
            nn.PReLU(),
            nn.Conv2d(cnum, cnum*2, 3, stride=2, padding=1),
            nn.BatchNorm2d(cnum*2),
            nn.PReLU(),
            nn.Conv2d(cnum*2, cnum*4, 3, stride=2, padding=1),
            nn.BatchNorm2d(cnum*4),
            nn.PReLU(),
            nn.Flatten(),
            nn.Linear(cnum*4*12*12, 1024)
        )
        self.fc_z = nn.Linear(1024, cnum*4*12*12)
        # 解码器
        self.decoder = nn.Sequential(
            nn.PReLU(),
            nn.ConvTranspose2d(cnum*4, cnum*2, 3, stride=2, padding=1, output_padding=1),
            nn.BatchNorm2d(cnum*2),
            nn.PReLU(),
            nn.ConvTranspose2d(cnum*2, cnum, 3, stride=2, padding=1, output_padding=1),
            nn.BatchNorm2d(cnum),
            nn.PReLU(),
            nn.ConvTranspose2d(cnum, 3, 3, stride=2, padding=1, output_padding=1),
        )

    def forward(self, x):
        z = self.encoder(x)
        x = self.fc_z(z)
        x = x.view(x.size(0), self.cnum*4, 12, 12)
        x = self.decoder(x)

        return x

# 物理约束损失函数
def compute_physical_loss(f, grad_z):
    Hx_x = torch.gradient(f[:,0], dim=2)[0]
    Hy_y = torch.gradient(f[:,1], dim=1)[0]
    div_mag = torch.stack([Hx_x, Hy_y, grad_z[:,2]], dim=1)

    return torch.mean(torch.abs(div_mag.sum(dim=1)))


if __name__ == "__main__":
    file_path = os.path.dirname(os.path.realpath(__file__))
    data_path = file_path + "/../../../data/tianchi_magfield_3D_96.h5"

    cfg = {
        "lr": 0.001,
        "batch_size": 32,
        "alpha": 0.1,
        "cnum": 16,
        "scaling": 10
    }

    # 初始化WandB
    wandb.init(config=cfg)
    cfg = wandb.config

    # 创建模型和优化器
    device = torch.device('cuda' if torch.cuda.is_available else 'cpu')
    model = CNNAE(cfg['cnum']).to(device)
    optimizer = optim.Adam(model.parameters(), lr=cfg['lr'])
    criterion = nn.MSELoss()

    # 数据加载和归一化
    with h5py.File(data_path, mode='r') as db:
        f = db['field']
    
        fx_z = np.gradient(f[:,0], axis=3)[:,:,:,1]
        fy_z = np.gradient(f[:,1], axis=3)[:,:,:,1]
        fz_z = np.gradient(f[:,2], axis=3)[:,:,:,1]
        grad_z = np.stack((fx_z, fy_z, fz_z), axis=1)
        grad_z = torch.from_numpy(grad_z.astype('float32')).to(device)
        f_t = torch.from_numpy(f[:,:,:,:,1].astype('float32'))

    train_data, test_data = train_test_split(f_t, test_size=0.2, random_state=42)
    train_loader = DataLoader(train_data * cfg['scaling'], batch_size=cfg['batch_size'], shuffle=False)
    num_epochs = 200

    # 训练
    for epoch in range(num_epochs):
        model.train()
        total_loss = 0.0
        it = 0

        for data in train_loader:

            data = data.to(device)
            optimizer.zero_grad()
            outputs = model(data)
            rec_loss = criterion(outputs, data)
            phys_loss = compute_physical_loss(outputs, grad_z[it*cfg['batch_size']:(it+1)*cfg['batch_size']])
            loss = rec_loss + cfg['alpha'] * phys_loss
            loss.backward()
            optimizer.step()

            total_loss += loss.item()
            it += 1

        train_loss = total_loss / (len(train_loader.dataset) / cfg['batch_size'])

        wandb.log({"loss": train_loss})
        print(f"Epoch [{epoch + 1}/{num_epochs}], Loss: {train_loss:.4f}")

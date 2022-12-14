import torch
import torchvision.transforms as transforms
from torchvision.utils import save_image
import torch.optim as optim

import sys
from glob import glob
import os
import argparse
import random
import numpy as np
import math
import cv2
import streamlit as st
import pickle
import matplotlib.pyplot as plt
import pandas as pd
from mpl_toolkits.mplot3d import axes3d
from matplotlib.animation import FuncAnimation
import plotly.express as px

models_path = os.path.dirname(os.getcwd())
sys.path.append(models_path)
from models.StyleGAN2 import StyledGenerator
from algorithms.gan_inversion import *
from algorithms.pca import *


############################## Arguments ##############################
parser = argparse.ArgumentParser(description='Invert StyleGAN')

parser.add_argument('--exp_detail', type=str, default='Invert StyleGAN')
parser.add_argument('--gpu_num', type=int, default=0)
parser.add_argument('--seed', type=int, default=100)

# Inverting
parser.add_argument('--latent_type', type=str, default='mean_style')
parser.add_argument('--iterations', type=int, default=6000)
parser.add_argument('--lr', type=float, default=1e-3)
parser.add_argument('--lpips_alpha', default=0.5, type=float)  # 0: Mean of FFHQ, 1: Independent
parser.add_argument('--mse_beta', default=0.5, type=float)  # 0: Mean of FFHQ, 1: Independent

# Mean Style
parser.add_argument('--style_mean_num', default=10, type=int)  # Style mean calculation for Truncation trick
parser.add_argument('--alpha', default=1, type=float)  # Fix=1: No progressive growing
parser.add_argument('--style_weight', default=0.7, type=float)  # 0: Mean of FFHQ, 1: Independent

# Transformations
parser.add_argument('--resize', type=bool, default=True)
parser.add_argument('--img_size', type=int, default=256)
parser.add_argument('--normalize', type=bool, default=True)
parser.add_argument('--mean', type=tuple, default=(0.5, 0.5, 0.5))
parser.add_argument('--std', type=tuple, default=(0.5, 0.5, 0.5))

opt = parser.parse_args()


############################## Functions ##############################
def run(inverter, img_path):
    img = inverter.read_img(img_path=img_path).to(inverter.device)
    img_numpy = cv2.cvtColor(cv2.imread(img_path, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    latent = inverter.initial_latent(latent_type=inverter.latent_type).to(inverter.device)
    latent.requires_grad = True
    optimizer = optim.Adam({latent}, lr=inverter.lr)
    # scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=400, eta_min=1e-04)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=400, T_mult=2, eta_min=1e-05)

    col1, col2 = st.columns(2)
    with col1:
        target_img = st.empty()
        target_img.image(img_numpy/255., use_column_width=True, caption='Target Image')
    with col2:
        image_location = st.empty()
    progress_bar = st.empty()

    # Show Coordinates
    coordinate = st.empty()

    # 3D plot
    example = st.empty()
    fig, ax = plt.subplots()
    ax.set_xlim(-7.5, 7.5)
    ax.set_ylim(-7.5, 7.5)

    if domain == 'FFHQ' or domain == 'celebs':
        x = [2.014, 2.044, -0.451, 4.177, 2.902, 5.982, 0.561, 3.144, 7.077, 3.388]
        y = [-3.259, -7.233, -0.840, 1.085, 2.730, 0.288, 0.290, -2.822, -2.917, 0.995]
        name = ['sohee', 'irene', 'jennie', 'jimin', 'jihoon', 'suhyeon', 'naeun', 'suzy', 'top', 'hun']
        ax.scatter(x, y)
        for i, n in enumerate(name):
            ax.annotate(n, (x[i], y[i]))

    # Pickle data
    if domain == 'celebs':
        with open('./pickle_data/pca(FFHQ).pickle', 'rb') as f:
            pickle_data = pickle.load(f)
    else:
        with open('./pickle_data/pca({}).pickle'.format(domain), 'rb') as f:
            pickle_data = pickle.load(f)

    # Transformer
    transformer = pickle_data['model']

    for iteration in range(1, inverter.iterations + 1):
        decoded_img = inverter.G.forward_from_style(style=latent, step=inverter.step, alpha=inverter.alpha,
                                                mean_style=inverter.mean_style, style_weight=inverter.style_weight)
        lpips_loss = inverter.lpips_criterion(decoded_img, img)
        mse_loss = inverter.MSE_criterion(decoded_img, img)
        loss = inverter.lpips_alpha * lpips_loss + inverter.mse_beta * mse_loss
        loss.backward()
        optimizer.step()

        # print('Iteration {} | total loss:{} | lpips loss:{}, mse loss:{}'.format(
        #     iteration, loss.item(), lpips_loss.item(), mse_loss.item()
        # ))

        reverse_transform = transforms.Compose([
            transforms.Normalize(mean=[-m / s for m, s in zip(inverter.mean, inverter.std)], std=[1 / s for s in inverter.std])
        ])
        sample = torch.squeeze(decoded_img, dim=0)
        sample = reverse_transform(sample)
        sample = sample.detach().cpu().numpy().transpose(1, 2, 0)
        sample = np.clip(sample, 0., 1.) * 255.
        sample = cv2.cvtColor(sample, cv2.COLOR_RGB2BGR)
        image_location.image(sample/255., use_column_width=True, caption='Prediction')
        progress_bar.progress(iteration / inverter.iterations)

        coord = get_coord(latent.detach().cpu(), transformer, n_axis=3)
        x, y, z = coord[0]
        coordinate.markdown('Coordinate | x:{:.3f} y:{:.3f}, z:{:.3f}'.format(x, y, z))

        if iteration % 100 == 1:
            # ax.scatter(x, y, z)
            plt.plot(x, y, 'ro')
            example.pyplot(fig)

        scheduler.step()

############################## Streamlit ##############################
if __name__ == '__main__':
    st.title('GAN Inversion')
    st.sidebar.title('Choose Variables')

    # Domain Select Box
    domain = st.sidebar.selectbox(label='Select Domain',
                                  options=['Dog', 'Cat', 'AFAD', 'FFHQ', 'celebs'])

    # STOP Button
    reset_button = st.sidebar.button("RESET")

    if domain == 'celebs':
        img_dir = os.path.join(os.getcwd(), 'sample_imgs/{}'.format(domain))
        img_paths = make_dataset(img_dir)
        names = [os.path.basename(p).split('.')[0] for p in img_paths]
        sample_img_name = st.sidebar.selectbox(label='Select Image', options=names)

        # Inverter
        inverter = Inverter(opt, domain='FFHQ')
        img_path = os.path.join('./sample_imgs/celebs', '{}.png'.format(sample_img_name))
        run(inverter=inverter, img_path=img_path)

    else:
        # Sample Image Selection
        img_dir = os.path.join(os.getcwd(), 'sample_imgs/{}'.format(domain))
        img_paths = make_dataset(img_dir)
        sample_img_name = st.sidebar.selectbox(label='Select Image', options=[i for i in range(1, len(img_paths))])

        # Inverter
        inverter = Inverter(opt, domain=domain)
        img_path = img_paths[sample_img_name]
        run(inverter=inverter, img_path=img_path)









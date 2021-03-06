import glob
import math
import os
import random

import numpy as np
import torch
import torchvision.transforms.functional as F
from PIL import Image
from imageio import imread
from skimage.color import rgb2gray, gray2rgb
from skimage.feature import canny
from torch.utils.data import DataLoader

import mask_generator
import utils
from .utils import create_mask


class Dataset(torch.utils.data.Dataset):
    def __init__(self, config, flist, edge_flist, mask_flist, augment=True, training=True):
        super(Dataset, self).__init__()
        self.mode = config.MODE
        self.augment = augment
        self.training = training
        self.dataset_path = config.DATASET_PATH
        self.data = self.load_flist(flist)  # 最好为n^2 的不带alpha的RGB图片
        self.edge_data = self.load_flist(edge_flist)
        self.mask_data = self.load_flist(mask_flist)  # 必须位图 类型为 Pil的 ‘1’

        self.input_size = config.INPUT_SIZE
        self.sigma = config.SIGMA
        self.edge = config.EDGE
        self.mask = config.MASK
        self.nms = config.NMS

        # in test mode, there's a one-to-one relationship between mask and image
        # masks are loaded non random
        if config.MODE == 2:
            self.mask = 6

        # preventing inaccurate ratio of img in train and losing details in test
        self.is_center_crop = True if self.mode == 1 else False

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        try:
            item = self.load_item(index)
        except:
            print('loading error: ' + self.data[index])
            item = self.load_item(0)

        return item

    def load_name(self, index):
        name = self.data[index]
        return os.path.basename(name)

    def load_item(self, index):
        size = self.input_size

        # load image
        ori_img = imread(self.data[index])
        img = np.array(ori_img)

        # gray to rgb
        if len(ori_img.shape) < 3:
            ori_img = gray2rgb(ori_img)

        # resize/crop if needed
        if size != 0:
            img = utils.resize(ori_img, size, size, self.is_center_crop)

        # create grayscale image
        img_gray = rgb2gray(img)

        # load mask
        mask = self.load_mask(img, index)

        # resize/crop if needed
        if size != 0:
            img_shape = img.shape
            mask = utils.resize(mask, img_shape[0], img_shape[1], self.is_center_crop)

        # load edge
        edge = self.load_edge(img_gray, index, mask)

        # augment data
        if self.augment and np.random.binomial(1, 0.5) > 0:
            img = img[:, ::-1, ...]
            img_gray = img_gray[:, ::-1, ...]
            edge = edge[:, ::-1, ...]
            mask = mask[:, ::-1, ...]

        return list(ori_img.shape), self.to_tensor(img), self.to_tensor(img_gray), self.to_tensor(edge), self.to_tensor(mask)

    def load_edge(self, img, index, mask):
        sigma = self.sigma

        # in test mode images are masked (with masked regions),
        # using 'mask' parameter prevents canny to detect edges for the masked regions
        mask = None if self.training else (1 - mask / 255).astype(np.bool)

        # canny
        if self.edge == 1:
            # no edge
            if sigma == -1:
                return np.zeros(img.shape).astype(np.float)

            # random sigma
            if sigma == 0:
                sigma = random.randint(1, 4)

            return canny(img, sigma=sigma, mask=mask).astype(np.float)

        # external
        else:
            imgh, imgw = img.shape[0:2]
            edge = imread(self.edge_data[index])
            edge = utils.resize(edge, imgh, imgw, self.is_center_crop)

            # non-max suppression
            if self.nms == 1:
                edge = edge * canny(img, sigma=sigma, mask=mask)

            return edge

    def load_mask(self, img, index):
        imgh, imgw = img.shape[0:2]
        mask_type = self.mask

        # external + random block
        if mask_type == 4:
            mask_type = 1 if np.random.binomial(1, 0.5) == 1 else 3

        # external + random block + half
        elif mask_type == 5:
            mask_type = np.random.randint(1, 4)

        # random block
        if mask_type == 1:
            # return create_mask(imgw, imgh, imgw // 2, imgh // 2)
            return mask_generator.generate_random_mask(np.random.rand(np.random.randint(2, 5), 8), imgh, imgw)

        # half
        if mask_type == 2:
            # randomly choose right or left
            return create_mask(imgw, imgh, imgw // 2, imgh, 0 if random.random() < 0.5 else imgw // 2, 0)

        # external
        if mask_type == 3:
            mask_index = random.randint(0, len(self.mask_data) - 1)
            mask = imread(self.mask_data[mask_index])
            mask = utils.resize(mask, imgh, imgw, self.is_center_crop)
            mask = (mask > 0).astype(np.uint8) * 255       # threshold due to interpolation
            return mask

        # test mode: load mask non random
        if mask_type == 6:
            mask = imread(self.mask_data[index%len(self.mask_data)])
            mask = utils.resize(mask, imgh, imgw, centerCrop=False)
            mask = rgb2gray(mask)
            mask = (mask > 0).astype(np.uint8) * 255
            return mask

    def to_tensor(self, img):
        img = Image.fromarray(img)
        img_t = F.to_tensor(img).float()
        return img_t

    def load_flist(self, flist):
        if isinstance(flist, list):
            return flist

        # flist: image file path, image directory path, text file flist path
        if isinstance(flist, str):
            file_path = os.path.join(self.dataset_path, flist)
            if os.path.isdir(file_path):
                flist = list(glob.glob(file_path + '/*.jpg')) + list(glob.glob(file_path + '/*.png'))
                flist.sort()
                return flist

            if os.path.isfile(file_path):
                try:
                    return np.genfromtxt(file_path, dtype=np.str, encoding='utf-8')
                except:
                    return [file_path]

        return []

    def create_iterator(self, batch_size):
        while True:
            sample_loader = DataLoader(
                dataset=self,
                batch_size=batch_size,
                drop_last=True
            )

            for item in sample_loader:
                yield item

'''
author: Feng Yidan
email: fengyidan1995@126.com
'''

import h5py
import cv2
import matplotlib.pyplot as plt
import numpy as np
import os

import pycocotools.mask as pm
import scipy.io as scio
import argparse
import json
import os
import numpy.ma as ma
from matplotlib.pyplot import *
import glob
import pickle
import open3d as o3d
import progressbar
import yaml
##
import low_solidity_support as loso
##


"""
prepare input images and annotation dicts from the virtual data output for training segmentation network
"""

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_type", type=str, default="gray", help='rgb or gray')
    parser.add_argument("--obj_idx", type=str, default="01")
    parser.add_argument("--virtual_dir", type=str, default='../example_virtual_data_output/', help='source path of virtual data, such as /research/d3/bqyang/yidan/dataset_output')
    parser.add_argument("--output_dir", type=str, default=None, help='output dir of converted data')
    parser.add_argument("--log_dir", type=str, default=None, help='output dir of converted data')
    parser.add_argument("--obj_path", type=str, default='./part01.obj', help='path of CAD model in .obj format')
    parser.add_argument("--phase", type=str, default='train')
    parser.add_argument("--oc", type=float, default=0.5, help='threshold for visibility (occlusion), only objects with oc > x will be labelled')
    return parser.parse_args()

# def object_back_projection(intrinsics, pc_selected, img_shape):
#     """
#     map the point cloud onto a 2D scene

#     args:
#     pc_selected: sampled point cloud from object CAD model, already translated by pose
#     img_shape:   shape of the 2D scene

#     return:
#     object mask without occlusion
#     """
#     h, w = img_shape
#     obj_mask = np.zeros((h, w), dtype=bool)
#     pc_non_zero = pc_selected[np.where(np.all(pc_selected[:, :] != [0.0, 0.0, 0.0], axis=-1) == True)[0]]
#     fx, fy = intrinsics[0, 0], intrinsics[1, 1]
#     cx, cy = intrinsics[0, 2], intrinsics[1, 2]
#     # depth = pc_non_zero[:, 2]  # point_z
#     coords_x = (pc_non_zero[:, 0] / pc_non_zero[:, 2] * fx + cx).astype(np.uint16)
#     coords_y = (pc_non_zero[:, 1] / pc_non_zero[:, 2] * fy + cy).astype(np.uint16)
#     # check index range:
#     new_x = np.delete(coords_x, coords_x>=w)
#     new_y = np.delete(coords_y, coords_x>=w)
#     new_x = np.delete(new_x, new_y>=h)
#     new_y = np.delete(new_y, new_y>=h)
#     obj_mask[new_y, new_x] = True
#     return obj_mask


def object_back_projection(intrinsics, pc_selected, img_shape):    
    """
    map the point cloud onto a 2D scene
    
    args:
    pc_selected: sampled point cloud from object CAD model, already translated by pose    
    img_shape:   shape of the 2D scene

    return:
    object mask without occlusion    
    """    
    h, w = img_shape    
    obj_mask = np.zeros((h+400, w+400), dtype=bool)    
    pc_non_zero = pc_selected[np.where(np.all(pc_selected[:, :] != [0.0, 0.0, 0.0], axis=-1) == True)[0]]    
    fx, fy = intrinsics[0, 0], intrinsics[1, 1]    
    cx, cy = intrinsics[0, 2], intrinsics[1, 2]    
    # depth = pc_non_zero[:, 2]  # point_z    
    coords_x = (pc_non_zero[:, 0] / pc_non_zero[:, 2] * fx + cx).astype(np.int16)    
    coords_y = (pc_non_zero[:, 1] / pc_non_zero[:, 2] * fy + cy).astype(np.int16)    
    # # check index range:    
    # new_x = np.delete(coords_x, coords_x>=w)    
    # new_y = np.delete(coords_y, coords_x>=w)    
    # new_x = np.delete(new_x, new_y>=h)    
    # new_y = np.delete(new_y, new_y>=h)    
    # obj_mask[new_y, new_x] = True    
    coords_x = np.clip(coords_x, -200, w+200-1)
    coords_y = np.clip(coords_y, -200, h+200-1)
    obj_mask[coords_y+200, coords_x+200] = True
    return obj_mask


def get_rotated_obj(bitmask):
    y_idxs, x_idxs = np.where(bitmask)
    object_points = np.array([[x, y] for x, y in zip(x_idxs, y_idxs)])
    (c_x, c_y), (w, h), a = cv2.minAreaRect(object_points)
    rle = pm.encode(np.asarray(bitmask, order="F"))
    rle['counts'] = rle['counts'].decode()
    obj = {
        "bbox": [int(c_x), int(c_y), int(w), int(h), -a],
        "segmentation": rle,
        "category_id": 0,
    }
    return obj

if __name__ == "__main__":
    args = get_args()

    loso.IS_LOW = loso.is_low_solidity(obj_path=args.obj_path, data_dir=args.virtual_dir, allow=True)
    print(f"loso flag: {loso.IS_LOW}")

    # get instance point cloud to compute visibility for each object in the scene
    obj_mesh = o3d.io.read_triangle_mesh(args.obj_path)
    model_pc = obj_mesh.sample_points_uniformly(number_of_points=200000)
    inst_model_pc = np.asarray(model_pc.points)

    data_dir = args.virtual_dir
    output_dir = "./" + args.obj_idx
    if args.output_dir is not None: output_dir = args.output_dir

    log_dir = output_dir + "/log"
    if args.log_dir is not None: log_dir = args.log_dir
    os.makedirs(log_dir, exist_ok=True)

    idx = 0
    dataset_dicts = []
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir,'train'), exist_ok=True)  # for saving training 2D images
    os.makedirs(os.path.join(output_dir,'json'), exist_ok=True)  # for saving detectron2 format annotaions

    h5_dir = os.path.join(data_dir, "compressed/")
    meta_dir = os.path.join(data_dir, "meta/")

    imgs_to_process = len(os.listdir(h5_dir))
    bar = progressbar
    for name in bar.progressbar(os.listdir(h5_dir)):
        record = {}
        file = h5py.File(os.path.join(h5_dir, name), 'r')
        if args.data_type == "rgb":
            rgb = file['rgb'][:]
            rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)
            rgb = (rgb * 255.0)
            rgb[rgb > 255] = 255
            output = rgb
        elif args.data_type == "gray":
            rgb = file['rgb'][:]
            gray = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
            gray = (gray * 255.0)
            gray[gray>255] = 255
            output = gray
        bit_label = file['label'][:]
        file.close()

        height, width = output.shape

        meta = scio.loadmat(meta_dir + name.split('.')[0] + ".mat")
        meta_name = meta['name']
        intrinsics = meta['intrinsic_matrix_CameraLeft']
        obj_list = range(len(meta['obj_idx'][0]))
        objs = []
        for obj_idx in obj_list:
            inst_mask = ma.getmaskarray(ma.masked_equal(bit_label, obj_idx + 1)) # occluded instance mask
            inst_r = meta['poses'][:, :, obj_idx][:, 0:3]
            inst_t = np.array([meta['poses'][:, :, obj_idx][:, 3:4].flatten()])
            if np.sum(inst_mask)<=0: continue
            # print("inst_r_t", inst_r, inst_t)
            inst_model_array = np.add(np.dot(inst_model_pc, inst_r.T), inst_t)
            inst_full_mask = object_back_projection(intrinsics, inst_model_array, bit_label.shape) # complete instance mask
            
            ##
            if np.sum(inst_full_mask)<=0:
                visibility = 0.
            else:
                visibility = np.sum(inst_mask) / np.sum(inst_full_mask)
            
            if visibility > args.oc:
                if loso.IS_LOW:
                    loso.add_part_obj(inst_mask, inst_full_mask, objs)
                else:
                    objs.append(get_rotated_obj(inst_mask))
            ##

        record["annotations"] = objs
        fname = str(idx) + '.png'
        cv2.imwrite(os.path.join(output_dir,'train', fname), output)
        record['file_name'] = os.path.join(output_dir, args.phase, fname)
        record['image_id'] = idx
        record['height'] = height
        record['width'] = width
        dataset_dicts.append(record)
        idx += 1
        with open(log_dir + "/progress.txt", "a") as f:
            print(f"Converted imgs to training format : {idx}/{imgs_to_process}")
    
    
    with open(os.path.join(output_dir, 'json', 'info.yaml'), 'w') as f:
        yaml.dump({
            "use_loso": loso.IS_LOW
        }, f)

    with open(os.path.join(output_dir, 'json', args.phase + '.json'), 'w') as f:
        json.dump(dataset_dicts, f)
        print("json file: %s accomplished!" % args.phase)

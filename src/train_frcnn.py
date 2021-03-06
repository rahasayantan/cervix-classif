import json
import os
import random
import sys
from math import ceil

import time
from keras import backend as K
from keras.callbacks import EarlyStopping, ModelCheckpoint, Callback
from keras.callbacks import ReduceLROnPlateau
from keras.layers import Input
from keras.models import Model
from keras.optimizers import Adam

from data_provider import ROI_DIR, ROI_CLASSES_FILE, ROI_BBOX_FILE
from keras_frcnn import config
from keras_frcnn import data_generators
from keras_frcnn import losses
from keras_frcnn import resnet as nn
from keras_frcnn.simple_parser import get_data

import fire

from model_utils import dump_args, LoggingCallback

sys.setrecursionlimit(40000)
random.seed(0)

C = config.Config()


def build_model(classes_count, num_anchors):
    if K.image_dim_ordering() == 'th':
        input_shape_img = (3, None, None)
    else:
        input_shape_img = (None, None, 3)

    img_input = Input(shape=input_shape_img)
    roi_input = Input(shape=(C.num_rois, 4))

    # define the base network (resnet here, can be VGG, Inception, etc)
    shared_layers = nn.nn_base(img_input, trainable=True)
    # define the RPN, built on the base layers
    rpn = nn.rpn(shared_layers, num_anchors)
    # the classifier is build on top of the base layers + the ROI pooling layer + extra layers
    classifier = nn.classifier(shared_layers, roi_input, C.num_rois, nb_classes=len(classes_count),
                               trainable=True)
    # define the full model
    model = Model([img_input, roi_input], rpn + classifier)
    try:
        print('loading weights from ', C.base_net_weights)
        if os.path.isfile(C.get_model_path()):
            model.load_weights(C.get_model_path(), by_name=True)
        else:
            model.load_weights(C.base_net_weights, by_name=True)
    except:
        print('Could not load pretrained model weights. Weights can be found at {} and {}'.format(
            'https://github.com/fchollet/deep-learning-models/releases/download/v0.2/resnet50_weights_th_dim_ordering_th_kernels_notop.h5',
            'https://github.com/fchollet/deep-learning-models/releases/download/v0.2/resnet50_weights_tf_dim_ordering_tf_kernels_notop.h5'
        ))
        exit()
    return model


@dump_args
def train(model_name, epochs=60, batch_size=1, lr=0.0001, decay=0.001):
    t = time.time()
    all_imgs, classes_count, class_mapping = get_data(ROI_BBOX_FILE)
    print("Parsing annotation files took " + str((time.time() - t) / 1000) + "s")

    num_anchors = len(C.anchor_box_scales) * len(C.anchor_box_ratios)

    C.model_name = model_name

    if 'bg' not in classes_count:
        classes_count['bg'] = 0
        class_mapping['bg'] = len(class_mapping)

    if not os.path.isfile(ROI_CLASSES_FILE):
        with open(ROI_CLASSES_FILE, 'w') as class_data_json:
            json.dump(class_mapping, class_data_json)

    print('Num classes (including bg) = {}'.format(len(classes_count)))
    random.shuffle(all_imgs)

    train_imgs = [s for s in all_imgs if s['imageset'] == 'trainval']
    val_imgs = [s for s in all_imgs if s['imageset'] == 'test']

    print('Num train samples {}'.format(len(train_imgs)))
    print('Num val samples {}'.format(len(val_imgs)))

    model = build_model(classes_count, num_anchors)

    optimizer = Adam(lr=lr, decay=decay)
    model.compile(optimizer=optimizer,
                  loss=[losses.rpn_loss_cls(num_anchors), losses.rpn_loss_regr(num_anchors),
                        losses.class_loss_cls,
                        losses.class_loss_regr(C.num_rois, len(classes_count) - 1)],
                  metrics={'dense_class_{}_loss'.format(len(classes_count)): 'accuracy'})

    data_gen_train = data_generators.get_anchor_gt(train_imgs, class_mapping, classes_count,
                                                   C, K.image_dim_ordering(), mode='train')

    data_gen_val = data_generators.get_anchor_gt(val_imgs, class_mapping, classes_count,
                                                 C, K.image_dim_ordering(), mode='val')

    callbacks = [EarlyStopping(monitor='val_loss', patience=20, verbose=0),
                 ModelCheckpoint(C.get_model_path(), monitor='val_loss', save_best_only=True, verbose=0),
                 ReduceLROnPlateau(monitor='loss', factor=0.1, patience=5, min_lr=1e-7, verbose=1),
                 LoggingCallback(C)]

    print('Starting training')
    model.fit_generator(data_gen_train, steps_per_epoch=ceil(len(train_imgs) / batch_size),
                        epochs=epochs, validation_data=data_gen_val,
                        validation_steps=ceil(len(train_imgs) / batch_size),
                        callbacks=callbacks, max_q_size=1, workers=1)


if __name__ == '__main__':
    fire.Fire()

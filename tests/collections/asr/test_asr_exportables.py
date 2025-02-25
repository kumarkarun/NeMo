# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import tempfile

import onnx
import pytest
import torch.cuda
from omegaconf import DictConfig, ListConfig

from nemo.collections.asr.models import (
    EncDecClassificationModel,
    EncDecCTCModel,
    EncDecRNNTModel,
    EncDecSpeakerLabelModel,
)
from nemo.collections.asr.modules import ConvASRDecoder, ConvASREncoder
from nemo.collections.asr.parts.utils import asr_module_utils
from nemo.core.utils import numba_utils
from nemo.core.utils.numba_utils import __NUMBA_MINIMUM_VERSION__

NUMBA_RNNT_LOSS_AVAILABLE = numba_utils.numba_cuda_is_supported(__NUMBA_MINIMUM_VERSION__)


class TestExportable:
    @pytest.mark.run_only_on('GPU')
    @pytest.mark.unit
    def test_EncDecCTCModel_export_to_onnx(self):
        model_config = DictConfig(
            {
                'preprocessor': DictConfig(self.preprocessor),
                'encoder': DictConfig(self.encoder_dict),
                'decoder': DictConfig(self.decoder_dict),
            }
        )
        model = EncDecCTCModel(cfg=model_config)
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.join(tmpdir, 'qn.onnx')
            model.export(output=filename)
            onnx_model = onnx.load(filename)
            onnx.checker.check_model(onnx_model, full_check=True)  # throws when failed
            assert onnx_model.graph.input[0].name == 'audio_signal'
            assert onnx_model.graph.output[0].name == 'logprobs'

    @pytest.mark.run_only_on('GPU')
    @pytest.mark.unit
    def test_EncDecClassificationModel_export_to_onnx(self, speech_classification_model):
        model = speech_classification_model.train()
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.join(tmpdir, 'edc.onnx')
            model.export(output=filename)
            onnx_model = onnx.load(filename)
            onnx.checker.check_model(onnx_model, full_check=True)  # throws when failed
            assert onnx_model.graph.input[0].name == 'audio_signal'
            assert onnx_model.graph.output[0].name == 'logits'

    @pytest.mark.run_only_on('GPU')
    @pytest.mark.unit
    def test_EncDecSpeakerLabelModel_export_to_onnx(self, speaker_label_model):
        model = speaker_label_model.train()
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.join(tmpdir, 'sl.onnx')
            model.export(output=filename)
            onnx_model = onnx.load(filename)
            onnx.checker.check_model(onnx_model, full_check=True)  # throws when failed
            assert onnx_model.graph.input[0].name == 'audio_signal'
            assert onnx_model.graph.output[0].name == 'logits'

    @pytest.mark.run_only_on('GPU')
    @pytest.mark.unit
    def test_EncDecCitrinetModel_export_to_onnx(self, citrinet_model):
        model = citrinet_model.train()
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.join(tmpdir, 'citri.onnx')
            model.export(output=filename)
            onnx_model = onnx.load(filename)
            onnx.checker.check_model(onnx_model, full_check=True)  # throws when failed
            assert onnx_model.graph.input[0].name == 'audio_signal'
            assert onnx_model.graph.input[1].name == 'length'
            assert onnx_model.graph.output[0].name == 'logprobs'

    @pytest.mark.run_only_on('GPU')
    @pytest.mark.unit
    def test_EncDecCitrinetModel_limited_SE_export_to_onnx(self, citrinet_model):
        model = citrinet_model.train()
        asr_module_utils.change_conv_asr_se_context_window(model, context_window=24, update_config=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.join(tmpdir, 'citri_se.onnx')
            model.export(output=filename, check_trace=True)
            onnx_model = onnx.load(filename)
            onnx.checker.check_model(onnx_model, full_check=True)  # throws when failed
            assert onnx_model.graph.input[0].name == 'audio_signal'
            assert onnx_model.graph.input[1].name == 'length'
            assert onnx_model.graph.output[0].name == 'logprobs'

    @pytest.mark.run_only_on('GPU')
    @pytest.mark.unit
    def test_EncDecRNNTModel_export_to_onnx(self, citrinet_rnnt_model):
        citrinet_rnnt_model.freeze()
        model = citrinet_rnnt_model.train()

        with tempfile.TemporaryDirectory() as tmpdir:
            fn = 'citri_rnnt.onnx'
            filename = os.path.join(tmpdir, fn)
            model.export(output=filename, verbose=False, check_trace=True)

            encoder_filename = os.path.join(tmpdir, 'Encoder-' + fn)
            assert os.path.exists(encoder_filename)
            onnx_model = onnx.load(encoder_filename)
            onnx.checker.check_model(onnx_model, full_check=True)  # throws when failed
            assert len(onnx_model.graph.input) == 2
            assert len(onnx_model.graph.output) == 2
            assert onnx_model.graph.input[0].name == 'audio_signal'
            assert onnx_model.graph.input[1].name == 'length'
            assert onnx_model.graph.output[0].name == 'outputs'
            assert onnx_model.graph.output[1].name == 'encoded_lengths'

            decoder_joint_filename = os.path.join(tmpdir, 'Decoder-Joint-' + fn)
            assert os.path.exists(decoder_joint_filename)
            onnx_model = onnx.load(decoder_joint_filename)
            onnx.checker.check_model(onnx_model, full_check=True)  # throws when failed

            input_examples = model.decoder.input_example()
            assert type(input_examples[-1]) == tuple
            num_states = len(input_examples[-1])
            state_name = list(model.decoder.output_types.keys())[-1]

            # enc_logits + (all decoder inputs - state tuple) + flattened state list
            assert len(onnx_model.graph.input) == (1 + (len(input_examples) - 1) + num_states)
            assert onnx_model.graph.input[0].name == 'encoder_outputs'
            assert onnx_model.graph.input[1].name == 'targets'
            assert onnx_model.graph.input[2].name == 'target_length'

            if num_states > 0:
                for idx, ip in enumerate(onnx_model.graph.input[3:]):
                    assert ip.name == "input-" + state_name + '-' + str(idx + 1)

            assert len(onnx_model.graph.output) == (len(input_examples) - 1) + num_states
            assert onnx_model.graph.output[0].name == 'outputs'
            assert onnx_model.graph.output[1].name == 'prednet_lengths'

            if num_states > 0:
                for idx, op in enumerate(onnx_model.graph.output[2:]):
                    assert op.name == "output-" + state_name + '-' + str(idx + 1)

    def setup_method(self):
        self.preprocessor = {
            'cls': 'nemo.collections.asr.modules.AudioToMelSpectrogramPreprocessor',
            'params': dict({}),
        }

        self.encoder_dict = {
            'cls': 'nemo.collections.asr.modules.ConvASREncoder',
            'params': {
                'feat_in': 64,
                'activation': 'relu',
                'conv_mask': True,
                'jasper': [
                    {
                        'filters': 1024,
                        'repeat': 1,
                        'kernel': [1],
                        'stride': [1],
                        'dilation': [1],
                        'dropout': 0.0,
                        'residual': False,
                        'separable': True,
                        'se': True,
                        'se_context_size': -1,
                    }
                ],
            },
        }

        self.decoder_dict = {
            'cls': 'nemo.collections.asr.modules.ConvASRDecoder',
            'params': {
                'feat_in': 1024,
                'num_classes': 28,
                'vocabulary': [
                    ' ',
                    'a',
                    'b',
                    'c',
                    'd',
                    'e',
                    'f',
                    'g',
                    'h',
                    'i',
                    'j',
                    'k',
                    'l',
                    'm',
                    'n',
                    'o',
                    'p',
                    'q',
                    'r',
                    's',
                    't',
                    'u',
                    'v',
                    'w',
                    'x',
                    'y',
                    'z',
                    "'",
                ],
            },
        }


@pytest.fixture()
def speech_classification_model():
    preprocessor = {'cls': 'nemo.collections.asr.modules.AudioToMelSpectrogramPreprocessor', 'params': dict({})}
    encoder = {
        'cls': 'nemo.collections.asr.modules.ConvASREncoder',
        'params': {
            'feat_in': 64,
            'activation': 'relu',
            'conv_mask': True,
            'jasper': [
                {
                    'filters': 32,
                    'repeat': 1,
                    'kernel': [1],
                    'stride': [1],
                    'dilation': [1],
                    'dropout': 0.0,
                    'residual': False,
                    'separable': True,
                    'se': True,
                    'se_context_size': -1,
                }
            ],
        },
    }

    decoder = {
        'cls': 'nemo.collections.asr.modules.ConvASRDecoderClassification',
        'params': {'feat_in': 32, 'num_classes': 30,},
    }

    modelConfig = DictConfig(
        {
            'preprocessor': DictConfig(preprocessor),
            'encoder': DictConfig(encoder),
            'decoder': DictConfig(decoder),
            'labels': ListConfig(["dummy_cls_{}".format(i + 1) for i in range(30)]),
        }
    )
    model = EncDecClassificationModel(cfg=modelConfig)
    return model


@pytest.fixture()
def speaker_label_model():
    preprocessor = {'cls': 'nemo.collections.asr.modules.AudioToMelSpectrogramPreprocessor', 'params': dict({})}
    encoder = {
        'cls': 'nemo.collections.asr.modules.ConvASREncoder',
        'params': {
            'feat_in': 64,
            'activation': 'relu',
            'conv_mask': True,
            'jasper': [
                {
                    'filters': 512,
                    'repeat': 1,
                    'kernel': [1],
                    'stride': [1],
                    'dilation': [1],
                    'dropout': 0.0,
                    'residual': False,
                    'separable': False,
                }
            ],
        },
    }

    decoder = {
        'cls': 'nemo.collections.asr.modules.SpeakerDecoder',
        'params': {'feat_in': 512, 'num_classes': 2, 'pool_mode': 'xvector', 'emb_sizes': [1024]},
    }

    modelConfig = DictConfig(
        {'preprocessor': DictConfig(preprocessor), 'encoder': DictConfig(encoder), 'decoder': DictConfig(decoder)}
    )
    speaker_model = EncDecSpeakerLabelModel(cfg=modelConfig)
    return speaker_model


@pytest.fixture()
def citrinet_model():
    preprocessor = {'cls': 'nemo.collections.asr.modules.AudioToMelSpectrogramPreprocessor', 'params': dict({})}
    encoder = {
        'cls': 'nemo.collections.asr.modules.ConvASREncoder',
        'params': {
            'feat_in': 80,
            'activation': 'relu',
            'conv_mask': True,
            'jasper': [
                {
                    'filters': 512,
                    'repeat': 1,
                    'kernel': [5],
                    'stride': [1],
                    'dilation': [1],
                    'dropout': 0.0,
                    'residual': False,
                    'separable': True,
                    'se': True,
                    'se_context_size': -1,
                },
                {
                    'filters': 512,
                    'repeat': 5,
                    'kernel': [11],
                    'stride': [2],
                    'dilation': [1],
                    'dropout': 0.1,
                    'residual': True,
                    'separable': True,
                    'se': True,
                    'se_context_size': -1,
                    'stride_last': True,
                    'residual_mode': 'stride_add',
                },
                {
                    'filters': 512,
                    'repeat': 5,
                    'kernel': [13],
                    'stride': [1],
                    'dilation': [1],
                    'dropout': 0.1,
                    'residual': True,
                    'separable': True,
                    'se': True,
                    'se_context_size': -1,
                },
                {
                    'filters': 640,
                    'repeat': 1,
                    'kernel': [41],
                    'stride': [1],
                    'dilation': [1],
                    'dropout': 0.0,
                    'residual': True,
                    'separable': True,
                    'se': True,
                    'se_context_size': -1,
                },
            ],
        },
    }

    decoder = {
        'cls': 'nemo.collections.asr.modules.ConvASRDecoder',
        'params': {'feat_in': 640, 'num_classes': 1024, 'vocabulary': list(chr(i % 28) for i in range(0, 1024))},
    }

    modelConfig = DictConfig(
        {'preprocessor': DictConfig(preprocessor), 'encoder': DictConfig(encoder), 'decoder': DictConfig(decoder)}
    )
    citri_model = EncDecSpeakerLabelModel(cfg=modelConfig)
    return citri_model


@pytest.fixture()
def citrinet_rnnt_model():
    labels = list(chr(i % 28) for i in range(0, 1024))
    model_defaults = {'enc_hidden': 640, 'pred_hidden': 256, 'joint_hidden': 320}

    preprocessor = {'cls': 'nemo.collections.asr.modules.AudioToMelSpectrogramPreprocessor', 'params': dict({})}
    encoder = {
        '_target_': 'nemo.collections.asr.modules.ConvASREncoder',
        'feat_in': 80,
        'activation': 'relu',
        'conv_mask': True,
        'jasper': [
            {
                'filters': 512,
                'repeat': 1,
                'kernel': [5],
                'stride': [1],
                'dilation': [1],
                'dropout': 0.0,
                'residual': False,
                'separable': True,
                'se': True,
                'se_context_size': -1,
            },
            {
                'filters': 512,
                'repeat': 5,
                'kernel': [11],
                'stride': [2],
                'dilation': [1],
                'dropout': 0.1,
                'residual': True,
                'separable': True,
                'se': True,
                'se_context_size': -1,
                'stride_last': True,
                'residual_mode': 'stride_add',
            },
            {
                'filters': 512,
                'repeat': 5,
                'kernel': [13],
                'stride': [1],
                'dilation': [1],
                'dropout': 0.1,
                'residual': True,
                'separable': True,
                'se': True,
                'se_context_size': -1,
            },
            {
                'filters': 640,
                'repeat': 1,
                'kernel': [41],
                'stride': [1],
                'dilation': [1],
                'dropout': 0.0,
                'residual': True,
                'separable': True,
                'se': True,
                'se_context_size': -1,
            },
        ],
    }

    decoder = {
        '_target_': 'nemo.collections.asr.modules.RNNTDecoder',
        'prednet': {'pred_hidden': 256, 'pred_rnn_layers': 1, 'dropout': 0.0},
    }

    joint = {
        '_target_': 'nemo.collections.asr.modules.RNNTJoint',
        'fuse_loss_wer': False,
        'jointnet': {'joint_hidden': 320, 'activation': 'relu', 'dropout': 0.0},
    }

    decoding = {'strategy': 'greedy_batch', 'greedy': {'max_symbols': 5}}

    modelConfig = DictConfig(
        {
            'preprocessor': DictConfig(preprocessor),
            'labels': labels,
            'model_defaults': DictConfig(model_defaults),
            'encoder': DictConfig(encoder),
            'decoder': DictConfig(decoder),
            'joint': DictConfig(joint),
            'decoding': DictConfig(decoding),
        }
    )
    citri_model = EncDecRNNTModel(cfg=modelConfig)
    return citri_model

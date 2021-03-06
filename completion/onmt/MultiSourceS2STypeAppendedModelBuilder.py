from typing import *

from onmt.encoders.CustomRNNEncoder import CustomRNNEncoder # DELETE
from onmt.encoders.AppendedRNNEncoder import AppendedRNNEncoder
from onmt.modules.MultiSourceCopyGenerator import MultiSourceCopyGenerator
from onmt.decoders.MultiSourceInputFeedRNNDecoder import MultiSourceInputFeedRNNDecoder
from onmt.models.MultiSourceTypeAppendedModel import MultiSourceTypeAppendedModel
import onmt
from onmt.decoders.decoder import DecoderBase # DELETE
from onmt.encoders import str2enc # DELETE
from onmt.encoders.encoder import EncoderBase
import onmt.inputters as inputters
from onmt.modules import Embeddings, VecEmbedding
from onmt.modules.util_class import Cast
from onmt.utils.misc import use_gpu
from onmt.utils.parse import ArgumentParser
import re
import torch
import torch.nn as nn
from torch.nn.init import xavier_uniform_

from seutil import LoggingUtils


class MultiSourceS2STypeAppendedModelBuilder:

    logger = LoggingUtils.get_logger(__name__)

    @classmethod
    def build_embeddings(cls,
            opt,
            text_field,
            for_encoder=True
    ) -> nn.Module:
        """
        Args:
            opt: the option in current environment.
            text_field(TextMultiField): word and feats field.
            for_encoder(bool): build Embeddings for encoder or decoder?
        """
        emb_dim = opt.src_word_vec_size if for_encoder else opt.tgt_word_vec_size

        if opt.model_type == "vec" and for_encoder:
            return VecEmbedding(
                opt.feat_vec_size,
                emb_dim,
                position_encoding=opt.position_encoding,
                dropout=(opt.dropout[0] if type(opt.dropout) is list
                         else opt.dropout),
            )

        pad_indices = [f.vocab.stoi[f.pad_token] for _, f in text_field]
        word_padding_idx, feat_pad_indices = pad_indices[0], pad_indices[1:]

        num_embs = [len(f.vocab) for _, f in text_field]
        num_word_embeddings, num_feat_embeddings = num_embs[0], num_embs[1:]

        fix_word_vecs = opt.fix_word_vecs_enc if for_encoder \
            else opt.fix_word_vecs_dec

        emb = Embeddings(
            word_vec_size=emb_dim,
            position_encoding=opt.position_encoding,
            feat_merge=opt.feat_merge,
            feat_vec_exponent=opt.feat_vec_exponent,
            feat_vec_size=opt.feat_vec_size,
            dropout=opt.dropout[0] if type(opt.dropout) is list else opt.dropout,
            word_padding_idx=word_padding_idx,
            feat_padding_idx=feat_pad_indices,
            word_vocab_size=num_word_embeddings,
            feat_vocab_sizes=num_feat_embeddings,
            sparse=opt.optim == "sparseadam",
            fix_word_vecs=fix_word_vecs
        )
        return emb

    @classmethod
    def build_encoder(cls, opt, embeddings, src_type, type_indx=None):
        """
        Various encoder dispatcher function.
        Args:
            opt: the option in current environment.
            embeddings (Embeddings): vocab embeddings for this encoder.
        """
        return AppendedRNNEncoder.from_opt(opt, embeddings, type_token_indx=type_indx)
        # if src_type=="l":
        #     return AppendedRNNEncoder.from_opt(opt, embeddings, type_token_indx=type_indx)
        # enc_type = opt.encoder_type if opt.model_type == "text" \
        #                                or opt.model_type == "vec" else opt.model_type
        # assert enc_type == "rnn" or enc_type == "brnn", "Only rnn or brnn encoders are supported for now"
        # return CustomRNNEncoder.from_opt(opt, embeddings)
    

    @classmethod
    def build_decoder(cls, opt, embeddings):
        """
        Various decoder dispatcher function.
        Args:
            opt: the option in current environment.
            embeddings (Embeddings): vocab embeddings for this decoder.
        """
        dec_type = "ifrnn" if opt.decoder_type == "rnn" and opt.input_feed \
            else opt.decoder_type

        assert dec_type == "ifrnn", "Only input feed rnn is supported"

        opt.src_types.pop(opt.src_types.index("type"))
        return MultiSourceInputFeedRNNDecoder.from_opt(opt, embeddings)
        # return str2dec[dec_type].from_opt(opt, embeddings)

    @classmethod
    def load_test_model(cls, src_types, opt, model_path=None):
        if model_path is None:
            model_path = opt.models[0]
        checkpoint = torch.load(model_path,
            map_location=lambda storage, loc: storage)

        model_opt = ArgumentParser.ckpt_model_opts(checkpoint['opt'])
        ArgumentParser.update_model_opts(model_opt)
        ArgumentParser.validate_model_opts(model_opt)
        vocab = checkpoint['vocab']

        if inputters.old_style_vocab(vocab):
            fields = inputters.load_old_vocab(
                vocab, opt.data_type, dynamic_dict=model_opt.copy_attn
            )
        else:
            fields = vocab

        model = cls.build_base_model(src_types, model_opt, fields, use_gpu(opt), checkpoint,
            opt.gpu)
        if opt.fp32:
            model.float()
        model.eval()
        model.generator.eval()
        return fields, model, model_opt

    @classmethod
    def build_base_model(cls,
            src_types: List[str],
            model_opt,
            fields,
            gpu,
            checkpoint=None,
            gpu_id=None
    ):
        """Build a model from opts.

        Args:
            model_opt: the option loaded from checkpoint. It's important that
                the opts have been updated and validated. See
                :class:`onmt.utils.parse.ArgumentParser`.
            fields (dict[str, torchtext.data.Field]):
                `Field` objects for the model.
            gpu (bool): whether to use gpu.
            checkpoint: the model gnerated by train phase, or a resumed snapshot
                        model from a stopped training.
            gpu_id (int or NoneType): Which GPU to use.

        Returns:
            the NMTModel.
        """
        # for back compat when attention_dropout was not defined
        try:
            model_opt.attention_dropout
        except AttributeError:
            model_opt.attention_dropout = model_opt.dropout

        # for finding type token indices
        type_token_dict = {"out-std-logic": fields["src.l"][0][1].vocab.stoi["out-std-logic"],
                           "out-std-logic-vector": fields["src.l"][0][1].vocab.stoi["out-std-logic-vector"],
                           "std-logic": fields["src.l"][0][1].vocab.stoi["std-logic"],
                           "std-logic-vector": fields["src.l"][0][1].vocab.stoi["std-logic-vector"],
                           "inout-std-logic": fields["src.l"][0][1].vocab.stoi["inout-std-logic"],
                           "inout-std-logic-vector": fields["src.l"][0][1].vocab.stoi["inout-std-logic-vector"],
                           "signed": fields["src.l"][0][1].vocab.stoi["signed"],
                           "unsigned": fields["src.l"][0][1].vocab.stoi["unsigned"],
                           "out-startaddr-array-type": fields["src.l"][0][1].vocab.stoi["out-startaddr-array-type"],
                           "std-ulogic": fields["src.l"][0][1].vocab.stoi["std-ulogic"],
                           "boolean": fields["src.l"][0][1].vocab.stoi["boolean"],
                           "<unk>": fields["src.l"][0][1].vocab.stoi["<unk>"],
                           "<pad>": fields["src.l"][0][1].vocab.stoi["<pad>"]
        }

        # Build embeddings.
        src_embs: Dict[str, Optional[nn.Module]] = dict()
        # PN: we always have text srcs for now
        for src_type in src_types:
            if src_type!="type":
                src_field = fields[f"src.{src_type}"]
                src_embs[src_type] = cls.build_embeddings(model_opt, src_field)
        # end for

        # Build encoders.
        encoders: Dict[str, EncoderBase] = dict()
        for src_i, src_type in enumerate(src_types):
            if src_type!="type":
                encoder = cls.build_encoder(model_opt, src_embs[src_type], src_type, type_indx=type_token_dict)
                encoders[src_type] = encoder
            # end if
        # end for

        # Build decoder.
        tgt_field = fields["tgt"]
        tgt_emb = cls.build_embeddings(model_opt, tgt_field, for_encoder=False)

        # No share embedding in this model
        assert not model_opt.share_embeddings, "share embeddings not supported"
        # # Share the embedding matrix - preprocess with share_vocab required.
        # if model_opt.share_embeddings:
        #     # src/tgt vocab should be the same if `-share_vocab` is specified.
        #     assert src_field.base_field.vocab == tgt_field.base_field.vocab, \
        #         "preprocess with -share_vocab if you use share_embeddings"
        #
        #     tgt_emb.word_lut.weight = src_emb.word_lut.weight

        decoder = cls.build_decoder(model_opt, tgt_emb)
        model_opt.src_types.append("type")

        # Build MultiSourceNMTModel(= encoders + decoder).
        if gpu and gpu_id is not None:
            device = torch.device("cuda", gpu_id)
        elif gpu and not gpu_id:
            device = torch.device("cuda")
        elif not gpu:
            device = torch.device("cpu")
        # end if
        model = MultiSourceTypeAppendedModel(encoders, decoder)
        # Build Generator.
        if not model_opt.copy_attn:
            if model_opt.generator_function == "sparsemax":
                gen_func = onmt.modules.sparse_activations.LogSparsemax(dim=-1)
            else:
                gen_func = nn.LogSoftmax(dim=-1)
            generator = nn.Sequential(
                nn.Linear(model_opt.dec_rnn_size,
                    len(fields["tgt"].base_field.vocab)),
                Cast(torch.float32),
                gen_func
            )
            if model_opt.share_decoder_embeddings:
                generator[0].weight = decoder.embeddings.word_lut.weight
        else:
            tgt_base_field = fields["tgt"].base_field
            vocab_size = len(tgt_base_field.vocab)
            pad_idx = tgt_base_field.vocab.stoi[tgt_base_field.pad_token]
            generator = MultiSourceCopyGenerator(model_opt.dec_rnn_size, vocab_size, pad_idx)

        # Load the model states from checkpoint or initialize them.
        if checkpoint is not None:
            # This preserves backward-compat for models using customed layernorm
            def fix_key(s):
                s = re.sub(r'(.*)\.layer_norm((_\d+)?)\.b_2',
                    r'\1.layer_norm\2.bias', s)
                s = re.sub(r'(.*)\.layer_norm((_\d+)?)\.a_2',
                    r'\1.layer_norm\2.weight', s)
                return s

            checkpoint['model'] = {fix_key(k): v
                for k, v in checkpoint['model'].items()}
            # end of patch for backward compatibility

            model.load_state_dict(checkpoint['model'], strict=False)
            generator.load_state_dict(checkpoint['generator'], strict=False)
        else:
            if model_opt.param_init != 0.0:
                for p in model.parameters():
                    p.data.uniform_(-model_opt.param_init, model_opt.param_init)
                for p in generator.parameters():
                    p.data.uniform_(-model_opt.param_init, model_opt.param_init)
            if model_opt.param_init_glorot:
                for p in model.parameters():
                    if p.dim() > 1:
                        xavier_uniform_(p)
                for p in generator.parameters():
                    if p.dim() > 1:
                        xavier_uniform_(p)

            for encoder in model.encoders:
                if hasattr(encoder, 'embeddings'):
                    encoder.embeddings.load_pretrained_vectors(
                        model_opt.pre_word_vecs_enc)
            if hasattr(model.decoder, 'embeddings'):
                model.decoder.embeddings.load_pretrained_vectors(
                    model_opt.pre_word_vecs_dec)

        model.generator = generator
        model.to(device)
        print(model)
        return model

    @classmethod
    def build_model(cls, src_types, model_opt, opt, fields, checkpoint):
        #cls.logger.info('Building model...')
        model = cls.build_base_model(src_types, model_opt, fields, use_gpu(opt), checkpoint)
        #cls.logger.info(model)
        return model

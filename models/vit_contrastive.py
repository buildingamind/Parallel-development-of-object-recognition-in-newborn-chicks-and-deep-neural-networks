# LIBRARIES
from argparse import ArgumentParser
from pytorch_lightning.metrics import Accuracy
import torch
import torch.nn
from torch.nn import functional as F
from torch import nn
import pytorch_lightning as pl
from torchmetrics import Accuracy
from vit_pytorch import ViT
from transformers import ViTConfig
import math



# enter the model configuration details 
class ViTConfigExtended(ViTConfig):
    def __init__(self, hidden_size=768,
        num_hidden_layers=1, 
        num_attention_heads=1, 
        intermediate_size=3072, 
        hidden_act="gelu",
        hidden_dropout_prob=0.0,
        attention_probs_dropout_prob=0.0,
        initializer_range=0.02,
        layer_norm_eps=1e-12,
        image_size=64,
        patch_size=8, 
        num_channels=3,
        num_classes: int = 512):
        super().__init__()
        self.num_classes = num_classes
        self.hidden_size = hidden_size
        self.intermediate_size = intermediate_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.patch_size = patch_size
        self.image_size = image_size
        self.num_channels = num_channels
        self.hidden_act = hidden_act
        self.hidden_dropout_prob = hidden_dropout_prob
        self.attention_probs_dropout_prob = attention_probs_dropout_prob
        self.initializer_range = initializer_range
        self.layer_norm_eps = layer_norm_eps


# configuration object for ViT hyper-paras
configuration = ViTConfigExtended()



class VisionTransformer(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.model = ViT(
            image_size = config.image_size,
            patch_size = config.patch_size,
            num_classes = config.num_classes,
            dim = config.hidden_size,
            depth = config.num_hidden_layers,
            heads = config.num_attention_heads,
            mlp_dim = config.intermediate_size,
            dropout = config.hidden_dropout_prob,
            emb_dropout = config.attention_probs_dropout_prob
        )
    
    @torch.no_grad()
    def init_weights(self):
        def _init(m):
            if isinstance(m, nn.Linear) or isinstance(m, nn.Conv2d):
                nn.init.xavier_uniform_(m.weight)
                if hasattr(m, 'bias') and m.bias is not None:
                    nn.init.normal_(m.bias, std=1e-6)
            
        self.apply(_init)
        nn.init.constant_(self.model.fc.weight, 0)
        nn.init.constant_(self.model.fc.bias, 0)
    
    def forward(self, x):
        return self.model(x)

class Backbone(torch.nn.Module):
    def __init__(self, model_type, config):
        super().__init__()
        if model_type == 'vit':
            self.model = VisionTransformer(config)
        
    def forward(self, x):
        return self.model(x)


class Projection(nn.Module):

    def __init__(self, input_dim=2048, hidden_dim=2048, output_dim=128, depth=1):
        super().__init__()
        self.output_dim = output_dim
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.depth = depth

        layers = []
        for i in range(depth):
            if i == 0:
                layers.append(nn.Linear(self.input_dim, self.hidden_dim))
            else:
                layers.append(nn.Linear(self.hidden_dim, self.hidden_dim))
            layers.append(nn.BatchNorm1d(self.hidden_dim))
            layers.append(nn.ReLU())

        if depth == 0:
            self.hidden_dim = self.input_dim

        layers.append(nn.Linear(self.hidden_dim, self.output_dim, bias=False))

        self.model = nn.Sequential(*layers)

    def forward(self, x):
        x = self.model(x)
        return F.normalize(x, dim=1)

class LitClassifier(pl.LightningModule):
    def __init__(
        self,
        backbone,
        loss_ver = 'v0',
        window_size = 3,
        learning_rate = 1e-3,
        hidden_mlp = 512,
        feat_dim = 128,
        hidden_depth = 1,
    ):
        super().__init__()
        self.save_hyperparameters()
        self.loss_ver = loss_ver
        self.window_size = window_size
        self.hidden_mlp = hidden_mlp
        self.feat_dim = feat_dim
        self.hidden_depth = hidden_depth
        self.learning_rate = learning_rate
        
        self.backbone = backbone   # ViT encoder
        
        self.projection = Projection(    # projection head, similar to SimCLR
            input_dim=self.hidden_mlp, # 512
            hidden_dim=self.hidden_mlp, # 512
            output_dim=self.feat_dim, # 128
            depth=self.hidden_depth, # 1
        )
        
        self.val_acc = Accuracy(compute_on_step=False)
        self.temperature = 0.5  # default from SimCLR contrastive model
        
    def forward(self, x):
        # SimCLR proposes to take the embeddings from the learned encoder
        feats = self.backbone(x)
        return feats
    
    
    def shared_step(self, batch):

        # loss version v0
        if self.loss_ver == 'v0':
            # PUSH TWO IMAGES TOGETHER IN THE EMBEDDING SPACE
            if self.window_size < 3:
                if len(batch) == 3:
                    img1, img2, _ = batch   # returns img1, img2, index

                else:
                    # final image in tuple is for online eval
                    (img1, img2, _), _ = batch

                # get h representations, bolts resnet returns a list
                h1 = self.backbone(img1)
                h2 = self.backbone(img2)

                # get z representations
                z1 = self.projection(h1)
                z2 = self.projection(h2)

                loss = self.nt_xent_loss(z1, z2, self.temperature)
            
            # PUSH MORE THAN TWO IMAGES TOGETHER IN THE EMBEDDING SPACE 
            else:
                #print(type(batch)) --> <class 'list'>
                #print(len(batch)) #len = 4 for window_size = 3 --> [img1, img2, img3, index]

                # if window_size 3
                if len(batch) == 4:
                    flag = 0
                    img1, img2, img3, _ = batch # [img1, img2, img3, index]

                # if window_size = 4
                else:
                    flag = 1
                    img1, img2, img3, img4, _ = batch # [img1, img2, img3, img4, index]
                    
                # get h representations, bolts resnet returns a list
                h1, h2, h3 = self.backbone(img1), self.backbone(img2), self.backbone(img3)

                    
                if flag == 1:
                    h4 = self.backbone(img4)
                    z4 = self.projection(h4)
                        

                # get z representations
                z1, z2, z3 = self.projection(h1), self.projection(h2), self.projection(h3)

                # push z1 and z2 together
                l1 = self.nt_xent_loss(z1,z2, self.temperature)
                # push z1 and z3 together
                l2 = self.nt_xent_loss(z1,z3, self.temperature)
                if flag == 1:
                    l3 = self.nt_xent_loss(z1,z4, self.temperature)
                        
                    # gather losses - 
                    loss = (l1+l2+l3)
                else:
                    # gather losses - 
                    loss = (l1+l2)
        
         # loss version v1
        else:
            # window_size 3
            img1, img2, img3, _ = batch

            # get h representations, bolts resnet returns a list
            h1, h2, h3 = self.backbone(img1), self.backbone(img2), self.backbone(img3)

            # get z representations
            z1, z2, z3 = self.projection(h1), self.projection(h2), self.projection(h3)

            loss = self.nt_xent_loss_triplet(z1, z2, z3, self.temperature)

        return loss

    def training_step(self, batch, batch_idx):
        #loss = self.step(batch, batch_idx)
        loss = self.shared_step(batch)
        self.log('train_loss', loss, on_epoch=True, on_step=True) # training_loss
        return loss

    def validation_step(self, batch, batch_idx):
        #loss = self.step(batch, batch_idx)
        loss = self.shared_step(batch)
        
        # TODO: log val_acc
        self.log('val_loss', loss, on_step=False, on_epoch=True) # for val_loss
        return loss

    def configure_optimizers(self):
        # self.hparams available because we called self.save_hyperparameters()
        return torch.optim.Adam(self.parameters(), lr=1e-4)
    
    # LOSS FUNCTION - 
    # Loss version 0 implementation
    def nt_xent_loss(self, out_1, out_2, temperature, eps=1e-6):
        """
            assume out_1 and out_2 are normalized
            out_1: [batch_size, dim]
            out_2: [batch_size, dim]
        """

        '''
        out_1.shape - [512, 128]
        dim is 128 because the output from projection head is of 128 dims.
        '''
        
        # gather representations in case of distributed training
        # out_1_dist: [batch_size * world_size, dim]
        # out_2_dist: [batch_size * world_size, dim]
 
        out_1_dist = out_1
        out_2_dist = out_2

        # out: [2 * batch_size, dim] -> [1024, 128]
        # out_dist: [2 * batch_size * world_size, dim] -> [1024, 128]
        out = torch.cat([out_1, out_2], dim=0)
        
        out_dist = torch.cat([out_1_dist, out_2_dist], dim=0)

        # cov and sim: [2 * batch_size, 2 * batch_size * world_size]
        # neg: [2 * batch_size]
        cov = torch.mm(out, out_dist.t().contiguous())  
        sim = torch.exp(cov / temperature) # denominator part of the loss function, since out_1 and out_2 is a batch that is passed, and not a single image.
        neg = sim.sum(dim=-1) # refer RM notes for understanding dimensionality


        # from each row, subtract e^1 to remove similarity measure for x1.x1
        row_sub = torch.Tensor(neg.shape).fill_(math.e).to(neg.device)
        neg = torch.clamp(neg - row_sub, min=eps)  # clamp for numerical stability

        # Positive similarity, pos becomes [2 * batch_size] -> numerator part of the loss function
        pos = torch.exp(torch.sum(out_1 * out_2, dim=-1) / temperature) # only mat_mul requires the column and rows to be similar. This is element by element multiplication
        pos = torch.cat([pos, pos], dim=0) 

        loss = -torch.log(pos / (neg + eps)).mean() # num/den

        return loss
    
    # Loss version 1 implementation
    def nt_xent_loss_triplet(self, out_1, out_2, out_3, temperature, eps=1e-6):
        """
        Normalized Temperature-scaled Cross Entropy (NT-Xent) loss for triplets of samples.
        
        Args:
        - out_1: Tensor of shape [batch_size, dim], first sample representations
        - out_2: Tensor of shape [batch_size, dim], second sample representations
        - out_3: Tensor of shape [batch_size, dim], third sample representations
        - temperature: Floating point value, temperature scaling factor
        - eps: Small value for numerical stability
        
        Returns:
        - loss: NT-Xent loss value
        """
        # Gather representations in case of distributed training (assuming no distributed training here)
        out_1_dist = out_1
        out_2_dist = out_2
        out_3_dist = out_3
        
        # Concatenate all samples into a single tensor
        out_all = torch.cat([out_1, out_2, out_3], dim=0)
        out_all_dist = torch.cat([out_1_dist, out_2_dist, out_3_dist], dim=0)
        
        # Calculate covariance and similarity
        cov = torch.mm(out_all, out_all_dist.t().contiguous())
        sim = torch.exp(cov / temperature)
        
        # Negative similarity (excluding self-similarity)
        neg = sim.sum(dim=-1)
        row_sub = torch.Tensor(neg.shape).fill_(math.e).to(neg.device)
        neg = torch.clamp(neg - row_sub, min=eps)  # clamp for numerical stability
        
        # Positive similarities (within the triplet)
        pos_ab = torch.exp(torch.sum(out_1 * out_2, dim=-1) / temperature)
        pos_ac = torch.exp(torch.sum(out_1 * out_3, dim=-1) / temperature)
        pos_bc = torch.exp(torch.sum(out_2 * out_3, dim=-1) / temperature)
        
        # Combine positive similarities
        pos = torch.cat([pos_ab, pos_ac, pos_bc], dim=0)
        
        # Calculate NT-Xent loss
        loss = -torch.log(pos / (neg + eps)).mean()
        
        return loss



    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = ArgumentParser(parents=[parent_parser], add_help=False)
        parser.add_argument('--learning_rate', type=float, default=0.0001)
        # transform params
        parser.add_argument("--gaussian_blur", action="store_true", help="add gaussian blur")
        parser.add_argument("--jitter_strength", type=float, default=0.5, help="jitter strength")
        parser.add_argument("--weight_decay", default=1e-6, type=float, help="weight decay")
        parser.add_argument("--start_lr", default=0, type=float, help="initial warmup learning rate")
        parser.add_argument("--final_lr", type=float, default=1e-6, help="final learning rate")
        parser.add_argument("--temperature", default=0.5, type=float, help="temperature parameter in training loss")
        parser.add_argument("--lars_wrapper", action='store_true', help="apple lars wrapper over optimizer used")
        parser.add_argument('--exclude_bn_bias', action='store_true', help="exclude bn/bias from weight decay")
        parser.add_argument("--warmup_epochs", default=5, type=int, help="number of warmup epochs")

        return parser
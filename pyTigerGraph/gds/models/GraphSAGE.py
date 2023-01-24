from . import base_model as bm
import torch
import torch.nn as nn
import torch.nn.functional as F
try:
    from torch_geometric.nn import to_hetero
    import torch_geometric.nn as gnn
except:
    raise Exception("PyTorch Geometric required to use GraphSAGE. Please install PyTorch Geometric")

class BaseGraphSAGEModel(bm.BaseModel):
    def __init__(self, num_layers, out_dim, dropout, hidden_dim, heterogeneous=None):
        super().__init__()
        self.dropout = dropout
        self.heterogeneous = heterogeneous
        tmp_model = gnn.GraphSAGE(-1, hidden_dim, num_layers, out_dim, dropout)
        if self.heterogeneous:
            self.model = to_hetero(tmp_model, heterogeneous)
        else:
            self.model = tmp_model

    def forward(self, batch):
        if self.heterogeneous:
            x = batch.x_dict
            for k in x.keys():
                x[k] = x[k].float()
            edge_index = batch.edge_index_dict
        else:
            x = batch.x.float()
            edge_index = batch.edge_index
        return self.model(x, edge_index)
    
    def compute_loss(self, loss_fn = None):
        raise NotImplementedError("Loss computation not implemented for BaseGraphSAGEModel")

class GraphSAGEForVertexClassification(BaseGraphSAGEModel):
    def __init__(self, num_layers, out_dim, dropout, hidden_dim, heterogeneous=None, class_weights=None):
        super().__init__(num_layers, out_dim, dropout, hidden_dim, heterogeneous)
        self.class_weight = class_weights

    def forward(self, batch, get_probs=False):
        logits = super().forward(batch)
        if get_probs:
            if self.heterogeneous:
                for k in logits.keys():
                    logits[k] = F.softmax(logits[k])
                return logits
            else:
                return F.softmax(logits)
        else:
            return logits

    def compute_loss(self, logits, batch, target_vertex_type=None, loss_fn = None):
        if not(loss_fn):
            loss_fn = F.cross_entropy
        if self.heterogeneous:
            loss = loss_fn(logits[target_vertex_type][batch[target_vertex_type].is_seed], 
                                   batch[target_vertex_type].y[batch[target_vertex_type].is_seed].long(),
                                   self.class_weight)
        else:
            loss = loss_fn(logits[batch.is_seed], batch.y[batch.is_seed].long(), self.class_weight)
        return loss

class GraphSAGEForVertexRegression(BaseGraphSAGEModel):
    def __init__(self, num_layers, out_dim, dropout, hidden_dim, heterogeneous=None, class_weights=None):
        super().__init__(num_layers, out_dim, dropout, hidden_dim, heterogeneous)
        self.class_weight = class_weights

    def forward(self, batch, get_probs=False):
        logits = super().forward(batch)
        if get_probs:
            if self.heterogeneous:
                for k in logits.keys():
                    logits[k] = F.softmax(logits[k])
                return logits
            else:
                return F.softmax(logits)
        else:
            return logits

    def compute_loss(self, logits, batch, target_vertex_type=None, loss_fn=None):
        if not(loss_fn):
            loss_fn = F.mse_loss
        if self.heterogeneous:
            loss = loss_fn(logits[target_vertex_type][batch[target_vertex_type].is_seed], 
                                   batch[target_vertex_type].y[batch[target_vertex_type].is_seed].long())
        else:
            loss = loss_fn(logits[batch.is_seed], batch.y[batch.is_seed].long())
        return loss


class GraphSAGEForLinkPrediction(BaseGraphSAGEModel):
    def __init__(self, num_layers, out_dim, dropout, hidden_dim, heterogeneous=None):
        super().__init__(num_layers, out_dim, dropout, hidden_dim, heterogeneous)

    def decode(self, src_z, dest_z, pos_edge_index, neg_edge_index):
        edge_index = torch.cat([pos_edge_index, neg_edge_index], dim=-1) # concatenate pos and neg edges
        logits = (src_z[edge_index[0]] * dest_z[edge_index[1]]).sum(dim=-1)  # dot product 
        return logits

    def get_link_labels(self, pos_edge_index, neg_edge_index):
        E = pos_edge_index.size(1) + neg_edge_index.size(1)
        link_labels = torch.zeros(E, dtype=torch.float)
        link_labels[:pos_edge_index.size(1)] = 1.
        return link_labels

    def generate_edges(self, batch, target_edge_type=None):
        if self.heterogeneous:
            pos_edges = batch[target_edge_type].edge_index[:, batch[target_edge_type].is_seed]
            src_neg_edges = torch.randint(0, batch[target_edge_type[0]].x.shape[0], (pos_edges.shape[1],), dtype=torch.long)
            dest_neg_edges = torch.randint(0, batch[target_edge_type[-1]].x.shape[0], (pos_edges.shape[1],), dtype=torch.long)
            neg_edges = torch.stack((src_neg_edges, dest_neg_edges))
        else:
            pos_edges = batch.edge_index[:, batch.is_seed]
            neg_edges = torch.randint(0, batch.x.shape[0], pos_edges.size(), dtype=torch.long)
        return pos_edges, neg_edges

    def compute_loss(self, logits, batch, target_edge_type=None, loss_fn=None):
        if self.heterogeneous:
            pos_edges, neg_edges = self.generate_edges(batch, target_edge_type)
            src_h = logits[target_edge_type[0]]
            dest_h = logits[target_edge_type[-1]]
            h = self.decode(src_h, dest_h, pos_edges, neg_edges)
        else:
            pos_edges, neg_edges = self.generate_edges(batch)
            h = self.decode(logits, logits, pos_edges, neg_edges)
        labels = self.get_link_labels(pos_edges, neg_edges)
        loss = F.binary_cross_entropy_with_logits(h, labels)
        return loss
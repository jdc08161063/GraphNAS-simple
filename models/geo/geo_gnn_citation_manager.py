import os.path as osp
import time

import numpy as np
import torch
import torch.nn.functional as F
import torch_geometric.transforms as T
from torch_geometric.datasets import Planetoid

from models.geo.geo_gnn import GraphNet
from models.gnn_citation_manager import CitationGNN, evaluate
from models.model_utils import EarlyStop


def load_data(dataset="Cora"):
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data', dataset)
    dataset = Planetoid(path, dataset, T.NormalizeFeatures())
    return dataset[0]


class GeoCitationManager(CitationGNN):
    def __init__(self, args):
        super(GeoCitationManager, self).__init__(args)

        self.data = load_data(args.dataset)
        self.args.in_feats = self.in_feats = self.data.num_features
        self.args.num_class = self.n_classes = self.data.y.max().item() + 1
        device = torch.device('cuda' if args.cuda else 'cpu')
        self.data.to(device)

    def build_gnn(self, actions):
        model = GraphNet(actions, self.in_feats, self.n_classes, drop_out=self.args.in_drop, multi_label=False,
                         batch_normal=False, residual=False)
        return model

    def save_param(self, model, update_all=False):
        pass

    @staticmethod
    def run_model(model, optimizer, loss_fn, data, epochs, early_stop=5, tmp_model_file="geo_citation.pkl",
                  half_stop_score=0, return_best=False, cuda=True, need_early_stop=False):

        early_stop_manager = EarlyStop(early_stop)
        # initialize graph
        dur = []
        begin_time = time.time()
        saved = False
        best_performance = 0
        for epoch in range(1, epochs + 1):
            should_break = False
            model.train()
            t0 = time.time()
            # forward
            logits = model(data.x, data.edge_index)
            logits = F.log_softmax(logits, 1)
            loss = loss_fn(logits[data.train_mask], data.y[data.train_mask])
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            # evaluate
            model.eval()
            logits = model(data.x, data.edge_index)
            logits = F.log_softmax(logits, 1)
            train_acc = evaluate(logits, data.y, data.train_mask)
            train_loss = float(loss)
            dur.append(time.time() - t0)

            val_loss = float(loss_fn(logits[data.val_mask], data.y[data.val_mask]))
            val_acc = evaluate(logits, data.y, data.val_mask)
            test_acc = evaluate(logits, data.y, data.test_mask)
            if test_acc > best_performance:
                best_performance = test_acc
            print(
                "Epoch {:05d} | Loss {:.4f} | Time(s) {:.4f} | acc {:.4f} | val_acc {:.4f} | test_acc {:.4f}".format(
                    epoch, loss.item(), np.mean(dur), train_acc, val_acc, test_acc))

            end_time = time.time()
            print("Each Epoch Cost Time: %f " % ((end_time - begin_time) / epoch))
            # print("Test Accuracy {:.4f}".format(acc))
            if early_stop_manager.should_save(train_loss, train_acc, val_loss, val_acc):
                saved = True
                torch.save(model.state_dict(), tmp_model_file)
            if need_early_stop and early_stop_manager.should_stop(train_loss, train_acc, val_loss, val_acc):
                should_break = True
            if should_break and epoch > 50:
                print("early stop")
                break
            if half_stop_score > 0 and epoch > (epochs / 2) and val_acc < half_stop_score:
                print("half_stop")
                break
        if saved:
            model.load_state_dict(torch.load(tmp_model_file))
        model.eval()
        val_acc = evaluate(model(data.x, data.edge_index), data.y, data.val_mask)
        print(evaluate(model(data.x, data.edge_index), data.y, data.test_mask))
        if return_best:
            return model, val_acc, best_performance
        else:
            return model, val_acc
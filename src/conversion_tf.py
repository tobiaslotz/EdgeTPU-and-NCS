from hummingbird.ml import convert
import tensorflow as tf
''' 
Module isnt edgetpu compatible yet because of unsupported ops like matmul. 
TODO 
1. replace unsupported ops 
2. address the loss of accuracy when using int8 instead of float32
'''
tf.config.run_functions_eagerly(True)

BATCH_SIZE = 1

class GEMMDecisionTreeImpl(tf.Module):

    def __init__(self, skl_model):
        self.container = convert(skl_model, 'torch', extra_config={"tree_implementation":"gemm"})
        self.op = self.container.model._operators[0]

    # input signature shape (batch_size, n_features)
    @tf.function(input_signature=[tf.TensorSpec(shape=(BATCH_SIZE, 8), dtype=tf.float32)])
    def __call__(self, x, train=False):
        x = tf.transpose(x)
        
        decision_cond = tf.math.less_equal
        if self.op.decision_cond.__name__ == 'le':
            decision_cond = tf.math.less_equal
        elif self.op.decision_cond.__name__ == 'ge':
            decision_cond = tf.math.greater_equal
        elif self.op.decision_cond.__name__ == 'lt':
            decision_cond = tf.math.less
        elif self.op.decision_cond.__name__ == 'gt':
            decision_cond = tf.math.greater
        elif self.op.decision_cond.__name__ == 'eq':
            decision_cond = tf.math.equal
        else:
            decision_cond = tf.math.not_equal
        
        x = decision_cond(tf.linalg.matmul(self.op.weight_1.detach().numpy(), x), self.op.bias_1.detach().numpy())
        x = tf.reshape(x, (self.op.n_trees, self.op.hidden_one_size, -1))

        x = tf.cast(x, dtype=tf.float32)

        x = tf.linalg.matmul(self.op.weight_2.detach().numpy(), x)

        x = tf.reshape(x, (self.op.n_trees * self.op.hidden_two_size, -1)) == self.op.bias_2.detach().numpy()
        x = tf.reshape(x, (self.op.n_trees, self.op.hidden_two_size, -1))

        x = tf.cast(x, dtype=tf.float32)

        x = tf.linalg.matmul(self.op.weight_3.detach().numpy(), x)
        x = tf.reshape(x, (self.op.n_trees, self.op.hidden_three_size, -1))

        x = tf.transpose(tf.reduce_sum(x, 0))

        return tf.math.argmax(x, axis=1), x


class TreeTraversalDecisionTreeImpl(tf.Module):
    def _expand_indexes(self, batch_size):
        indexes = self.op.nodes_offset
        indexes = indexes.expand(batch_size, self.op.num_trees)
        return indexes.detach().numpy().reshape(-1)
    
    def __init__(self, skl_model):
        self.container = convert(skl_model, 'torch', extra_config={"tree_implementation":"tree_trav"})
        self.op = self.container.model._operators[0]

    # input signature shape (batch_size, n_features)
    @tf.function(input_signature=[tf.TensorSpec(shape=(BATCH_SIZE, 8), dtype=tf.float32)])
    def __call__(self, x):
        #indexes = self._expand_indexes(tf.shape(x).numpy()[0])
        indexes = self._expand_indexes(BATCH_SIZE)

        decision_cond = tf.math.less_equal
        if self.op.decision_cond.__name__ == 'le':
            decision_cond = tf.math.less_equal
        elif self.op.decision_cond.__name__ == 'ge':
            decision_cond = tf.math.greater_equal
        elif self.op.decision_cond.__name__ == 'lt':
            decision_cond = tf.math.less
        elif self.op.decision_cond.__name__ == 'gt':
            decision_cond = tf.math.greater
        elif self.op.decision_cond.__name__ == 'eq':
            decision_cond = tf.math.equal
        else:
            decision_cond = tf.math.not_equal

        for _ in range(self.op.max_tree_depth):
            tree_nodes = indexes
            feature_nodes = tf.reshape(tf.gather(self.op.features.detach(), axis=0, indices=tree_nodes), [-1, self.op.num_trees])
            feature_values = tf.gather(x, feature_nodes, axis=1)

            thresholds = tf.reshape(tf.gather(self.op.thresholds.detach(), indexes, axis=0), [-1, self.op.num_trees])
            lefts = tf.reshape(tf.gather(self.op.lefts.detach(), indexes, axis=0), [-1, self.op.num_trees])
            rights = tf.reshape(tf.gather(self.op.rights.detach(), indexes, axis=0), [-1, self.op.num_trees])

            indexes = tf.cast(tf.where(decision_cond(feature_values, thresholds), lefts, rights), dtype=tf.int64)
            indexes = indexes + self.op.nodes_offset.detach()
            indexes = tf.reshape(indexes, [-1,])

        output = tf.reshape(tf.gather(self.op.values.detach(), indexes,axis=0), [-1, self.op.num_trees, self.op.n_classes])

        output = tf.reduce_sum(output, 1)

        return tf.math.argmax(output, axis=1), output


class PerfectTreeTraversalDecisionTreeImpl(tf.Module):
    def __init__(self, skl_model):
        self.container = convert(skl_model, 'torch', extra_config={"tree_implementation":"perf_tree_trav"})
        self.op = self.container.model._operators[0]

    @tf.function(input_signature=[tf.TensorSpec(shape=(300, 8), dtype=tf.float32)])
    def __call__(self, x):
        pass
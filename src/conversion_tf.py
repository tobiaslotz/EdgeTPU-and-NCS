from hummingbird.ml import convert
import tensorflow as tf
import numpy as np

tf.config.run_functions_eagerly(True)

BATCH_SIZE = 1
N_FEATURES = 8

''' 
3 Hummingbird Tree Translation methods implemented as tensorflow modules. These modules are tflite 
convertible but are not fully edgetpu compatible so they get partially mapped to cpu instead.
'''
class GEMMDecisionTreeImpl(tf.Module):

    def __init__(self, skl_model):
        self.container = convert(skl_model, 'torch', extra_config={"tree_implementation":"gemm"})
        self.op = self.container.model._operators[0]

    # input signature shape (batch_size, n_features)
    @tf.function(input_signature=[tf.TensorSpec(shape=(BATCH_SIZE, N_FEATURES), dtype=tf.float32)])
    def __call__(self, x):
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
    @tf.function(input_signature=[tf.TensorSpec(shape=(BATCH_SIZE, N_FEATURES), dtype=tf.float32)])
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

    @tf.function(input_signature=[tf.TensorSpec(shape=(BATCH_SIZE, N_FEATURES), dtype=tf.float32)])
    def __call__(self, x):
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
        
        prev_indices = tf.cast((decision_cond(tf.gather(x, self.op.root_nodes, axis=1), self.op.root_biases)), tf.int64)
        prev_indices = prev_indices + self.op.tree_indices
        prev_indices = tf.reshape(prev_indices, [-1,])

        factor = 2
        for nodes, biases in zip(self.op.nodes, self.op.biases):
            gather_indices = tf.reshape(tf.gather(nodes, prev_indices, axis=0), [-1, self.op.num_trees])
            features = tf.reshape(tf.gather(x, gather_indices, axis=1), [-1,])
            prev_indices = (
                factor * prev_indices + tf.cast(decision_cond(features, tf.gather(biases, prev_indices, axis=0)), tf.int64)
            )

        output = tf.reshape(tf.gather(self.op.leaf_nodes, prev_indices, axis=0), [-1, self.op.num_trees, self.op.n_classes])

        output = tf.math.reduce_sum(output, axis=1)

        return tf.math.argmax(output, axis=1), output

'''
SOON: Edgetpu compatible tensorflow modules that fully map onto the edgetpu
With numpy.matmul it is possible to multiply 2 int8 matrices, but it is currently not possible to
convert this model to tflite, since tflite doesnt support np.matmul.
TODO Find a way to fully int8 quantize model while being supported by tflite and being supported by
edge tpu whitout suffering significant information loss
'''
class GEMMDecisionTreeImplEdgeTPU(tf.Module):

    def __init__(self, skl_model):
        self.container = convert(skl_model, 'torch', extra_config={"tree_implementation":"gemm"})
        self.op = self.container.model._operators[0]

    # input signature shape (batch_size, n_features)
    @tf.function(input_signature=[tf.TensorSpec(shape=(BATCH_SIZE, N_FEATURES), dtype=tf.int8)])
    def __call__(self, x):
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
        
        x = decision_cond(tf.numpy_function(np.matmul, [tf.cast(self.op.weight_1.detach(), tf.int8), x], tf.int8) * 10, tf.cast(self.op.bias_1.detach() * 10, tf.int8))
        x = tf.reshape(x, (self.op.n_trees, self.op.hidden_one_size, -1))

        x = tf.cast(x, dtype=tf.int8)

        x = tf.numpy_function(np.matmul, [tf.cast(self.op.weight_2.detach(), tf.int8), x], tf.int8)

        x = tf.reshape(x, (self.op.n_trees * self.op.hidden_two_size, -1)) == tf.cast(self.op.bias_2.detach(), tf.int8)
        x = tf.reshape(x, (self.op.n_trees, self.op.hidden_two_size, -1))

        x = tf.cast(x, dtype=tf.int8)

        x = tf.numpy_function(np.matmul, [tf.cast(self.op.weight_3.detach() * 10000, tf.int8), x], tf.int8)
        x = tf.reshape(x, (self.op.n_trees, self.op.hidden_three_size, -1))

        x = tf.cast(x, tf.int32)

        x = tf.transpose(tf.reduce_sum(x, 0))

        return tf.math.argmax(x, axis=1), x
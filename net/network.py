import numpy as np
import tensorflow as tf

DEFAULT_PADDING = 'SAME'


def layer(op):
    '''Decorator for composable network layers.'''

    def layer_decorated(self, *args, **kwargs):
        # Automatically set a name if not provided.
        name = kwargs.setdefault('name', self.get_unique_name(op.__name__))
        # Figure out the layer inputs.
        if len(self.terminals) == 0:
            raise RuntimeError('No input variables found for layer %s.' % name)
        elif len(self.terminals) == 1:
            layer_input = self.terminals[0]
        else:
            layer_input = list(self.terminals)
        # Perform the operation and get the output.
        layer_output = op(self, layer_input, *args, **kwargs)
        # Add to layer LUT.
        self.layers[name] = layer_output
        # This output is now the input for the next layer.
        self.feed(layer_output)
        # Return self for chained calls.
        return self

    return layer_decorated


class Network(object):

    def __init__(self, inputs, initial_weights=None, trainable=True):
        self.k=5
        # The input nodes for this network
        self.inputs = inputs
        # The current list of terminal nodes
        self.terminals = []
        # Mapping from layer names to layers
        self.layers = dict(inputs)
        # If true, the resulting variables are set as trainable
        self.trainable = trainable
        # Switch variable for dropout
        self.use_dropout = tf.placeholder_with_default(tf.constant(1.0),
                                                       shape=[],
                                                       name='use_dropout')
        if initial_weights:
            self.initial_weights = np.load(initial_weights)
        else:
            self.initial_weights=None
        self.setup()

    def setup(self):
        '''Construct the network. '''
        raise NotImplementedError('Must be implemented by the subclass.')

    def load(self, data_path, session, ignore_missing=False):
        '''Load network weights.
        data_path: The path to the numpy-serialized network weights
        session: The current TensorFlow session
        ignore_missing: If true, serialized weights for missing layers are ignored.
        '''
        data_dict = np.load(data_path,encoding="latin1").item()
        for op_name in data_dict:
            with tf.variable_scope(op_name, reuse=True):
                for param_name, data in data_dict[op_name].items():
                    try:
                        var = tf.get_variable(param_name)
                        session.run(var.assign(data))
                    except ValueError:
                        if not ignore_missing:
                            raise

    def feed(self, *args):
        '''Set the input(s) for the next operation by replacing the terminal nodes.
        The arguments can be either layer names or the actual layers.
        '''
        assert len(args) != 0
        self.terminals = []
        for fed_layer in args:
            if isinstance(fed_layer, str):
                try:
                    fed_layer = self.layers[fed_layer]
                except KeyError:
                    raise KeyError('Unknown layer name fed: %s' % fed_layer)
            self.terminals.append(fed_layer)
        return self

    def get_output(self):
        '''Returns the current network output.'''
        return self.terminals[-1]

    def get_unique_name(self, prefix):
        '''Returns an index-suffixed unique name for the given prefix.
        This is used for auto-generating layer names based on the type-prefix.
        '''
        ident = sum(t.startswith(prefix) for t, _ in self.layers.items()) + 1
        return '%s_%d' % (prefix, ident)

    def make_var(self, name, shape):
        '''Creates a new TensorFlow variable.'''
        if self.initial_weights:
            try:
                return tf.get_variable(name, trainable=self.trainable,
                                       initializer=tf.constant(self.initial_weights[name]))
            except KeyError:
                return tf.get_variable(name, shape, trainable=self.trainable)
        else:
            return tf.get_variable(name, shape, trainable=self.trainable)

    def validate_padding(self, padding):
        '''Verifies that the padding is one of the supported ones.'''
        assert padding in ('SAME', 'VALID')

    @layer
    def conv2(self,
                 input,
                 k_h,
                 k_w,
                 c_o,
                 s_h,
                 s_w,
                 name,
                 relu=True,
                 padding=DEFAULT_PADDING,
                 group=1,
                 biased=True):
            # Verify that the padding is acceptable
            self.validate_padding(padding)
            # Get the number of channels in the input
            c_i = input[0].get_shape()[-1]
            # Verify that the grouping parameter is valid
            assert c_i % group == 0
            assert c_o % group == 0
            # Convolution for a given input and kernel
            convolve = lambda i, k: tf.nn.conv2d(i, k, [1, s_h, s_w, 1], padding=padding)
            with tf.variable_scope(name) as scope:
                kernel = self.make_var(name+'_W', shape=[k_h, k_w, int(c_i) / group, c_o])
                if group == 1:
                    # This is the common-case. Convolve the input without any further complications.
                    template = convolve(input[0], kernel)
                    detection = convolve(input[1], kernel)
                    output = [template,detection]
                else:
                    # Split the input into groups and then convolve each of them independently
                    # input_groups = tf.split(3, group, input)
                    # kernel_groups = tf.split(3, group, kernel)
                    template_groups = tf.split(input[0],group,3)
                    detection_groups = tf.split(input[1],group,3)

                    kernel_groups = tf.split(kernel,group,3)

                    template_groups = [convolve(i, k) for i, k in zip(template_groups, kernel_groups)]
                    detection_groups = [convolve(i, k) for i, k in zip(detection_groups, kernel_groups)]
                    # Concatenate the groups
                    template = tf.concat(template_groups,3)
                    detection = tf.concat(detection_groups,3)
                    output=[template,detection]
                # Add the biases
                if biased:
                    biases = self.make_var(name+'_b', [c_o])
                    template = tf.nn.bias_add(output[0], biases)
                    detection = tf.nn.bias_add(output[1], biases)
                    output=[template,detection]
                if relu:
                    # ReLU non-linearity
                    template = tf.nn.relu(output[0])
                    detection = tf.nn.relu(output[1])
                    output=[template,detection]
                return output

    @layer
    def conv_3(self,
              input,
              k_h,
              k_w,
              c_o,
              s_h,
              s_w,
              name,
              relu=True,
              padding=DEFAULT_PADDING,
              group=1,
              biased=True):
        # Verify that the padding is acceptable
        self.validate_padding(padding)
        # Get the number of channels in the input
        c_i = input[0].get_shape()[-1]
        # Verify that the grouping parameter is valid
        assert c_i % group == 0
        assert c_o % group == 0
        # Convolution for a given input and kernel
        convolve = lambda i, k: tf.nn.conv2d(i, k, [1, s_h, s_w, 1], padding=padding)
        with tf.variable_scope(name) as scope:
            kernel_t = self.make_var(name + '_t', shape=[k_h, k_w, int(c_i) / group, c_o])
            kernel_d = self.make_var(name + '_d', shape=[k_h, k_w, int(c_i) / group, c_o])
            if group == 1:
                # This is the common-case. Convolve the input without any further complications.
                template = convolve(input[0], kernel_t)
                detection = convolve(input[1], kernel_d)
                output = [template, detection]

            # Add the biases
            if biased:
                biases = self.make_var(name + '_b', [c_o])
                template = tf.nn.bias_add(output[0], biases)
                detection = tf.nn.bias_add(output[1], biases)
                output = [template, detection]
            if relu:
                # ReLU non-linearity
                template = tf.nn.relu(output[0])
                detection = tf.nn.relu(output[1])
                output = [template, detection]
        return output
    @layer
    def conv1(self,
             input,
             k_h,
             k_w,
             c_o,
             s_h,
             s_w,
             name,
             relu=True,
             padding=DEFAULT_PADDING,
             group=1,
             biased=True,
             index=0):
        # Verify that the padding is acceptable
        input=input[index]
        self.validate_padding(padding)
        # Get the number of channels in the input
        c_i = input.get_shape()[-1]
        # Verify that the grouping parameter is valid
        assert c_i % group == 0
        assert c_o % group == 0
        # Convolution for a given input and kernel
        convolve = lambda i, k: tf.nn.conv2d(i, k, [1, s_h, s_w, 1], padding=padding)
        with tf.variable_scope(name) as scope:
            kernel = self.make_var(name+'_W', shape=[k_h, k_w, int(c_i) / group, c_o])
            if group == 1:
                # This is the common-case. Convolve the input without any further complications.
                output = convolve(input, kernel)
            else:
                # Split the input into groups and then convolve each of them independently
                # input_groups = tf.split(3, group, input)
                # kernel_groups = tf.split(3, group, kernel)
                input_groups = tf.split(input,group,3)
                kernel_groups = tf.split(kernel,group,3)
                output_groups = [convolve(i, k) for i, k in zip(input_groups, kernel_groups)]
                # Concatenate the groups
                output = tf.concat(output_groups,3)
            # Add the biases
            if biased:
                biases = self.make_var(name+'_b', [c_o])
                output = tf.nn.bias_add(output, biases)
            if relu:
                # ReLU non-linearity
                output = tf.nn.relu(output, name=scope.name)
            return output
    @layer
    def conv(self,
             input,
             k_h,
             k_w,
             c_o,
             s_h,
             s_w,
             name,
             relu=True,
             padding=DEFAULT_PADDING,
             group=1,
             biased=True):
        # Verify that the padding is acceptable
        self.validate_padding(padding)
        # Get the number of channels in the input
        c_i = input.get_shape()[-1]
        # Verify that the grouping parameter is valid
        assert c_i % group == 0
        assert c_o % group == 0
        # Convolution for a given input and kernel
        convolve = lambda i, k: tf.nn.conv2d(i, k, [1, s_h, s_w, 1], padding=padding)
        with tf.variable_scope(name) as scope:
            kernel = self.make_var(name+'_W', shape=[k_h, k_w, int(c_i) / group, c_o])
            if group == 1:
                # This is the common-case. Convolve the input without any further complications.
                output = convolve(input, kernel)
            else:
                # Split the input into groups and then convolve each of them independently
                # input_groups = tf.split(3, group, input)
                # kernel_groups = tf.split(3, group, kernel)
                input_groups = tf.split(input,group,3)
                kernel_groups = tf.split(kernel,group,3)
                output_groups = [convolve(i, k) for i, k in zip(input_groups, kernel_groups)]
                # Concatenate the groups
                output = tf.concat(output_groups,3)
            # Add the biases
            if biased:
                biases = self.make_var(name+'_b', [c_o])
                output = tf.nn.bias_add(output, biases)
            if relu:
                # ReLU non-linearity
                output = tf.nn.relu(output, name=scope.name)
            return output


    @layer
    def relu(self, input, name):
        return tf.nn.relu(input, name=name)

    @layer
    def max_pool2(self, input, k_h, k_w, s_h, s_w, name, padding=DEFAULT_PADDING):
        self.validate_padding(padding)
        template=tf.nn.max_pool(input[0],
                                  ksize=[1, k_h, k_w, 1],
                                  strides=[1, s_h, s_w, 1],
                                  padding=padding)
        detection=tf.nn.max_pool(input[1],
                                  ksize=[1, k_h, k_w, 1],
                                  strides=[1, s_h, s_w, 1],
                                  padding=padding)
        return [template,detection]
    @layer
    def max_pool(self, input, k_h, k_w, s_h, s_w, name, padding=DEFAULT_PADDING):
        self.validate_padding(padding)
        return tf.nn.max_pool(input,
                                  ksize=[1, k_h, k_w, 1],
                                  strides=[1, s_h, s_w, 1],
                                  padding=padding)

    @layer
    def avg_pool(self, input, k_h, k_w, s_h, s_w, name, padding=DEFAULT_PADDING):
        self.validate_padding(padding)
        return tf.nn.avg_pool(input,
                              ksize=[1, k_h, k_w, 1],
                              strides=[1, s_h, s_w, 1],
                              padding=padding,
                              name=name)

    @layer
    def lrn2(self, input, radius, alpha, beta, name, bias=1.0):
        template=tf.nn.local_response_normalization(input[0],
                                                  depth_radius=radius,
                                                  alpha=alpha,
                                                  beta=beta,
                                                  bias=bias)
        detection=tf.nn.local_response_normalization(input[1],
                                                  depth_radius=radius,
                                                  alpha=alpha,
                                                  beta=beta,
                                                  bias=bias)

        return [template,detection]
    @layer
    def lrn(self, input, radius, alpha, beta, name, bias=1.0):
        return tf.nn.local_response_normalization(input,
                                                  depth_radius=radius,
                                                  alpha=alpha,
                                                  beta=beta,
                                                  bias=bias)
    @layer
    def concat(self, inputs, axis, name):
        return tf.concat(concat_dim=axis, values=inputs, name=name)

    @layer
    def add(self, inputs, name):
        return tf.add_n(inputs, name=name)

    @layer
    def fc(self, input, num_out, name, relu=True):
        with tf.variable_scope(name) as scope:
            input_shape = input.get_shape()
            if input_shape.ndims == 4:
                # The input is spatial. Vectorize it first.
                dim = 1
                for d in input_shape[1:].as_list():
                    dim *= d
                feed_in = tf.reshape(input, [-1, dim])
            else:
                feed_in, dim = (input, input_shape[-1].value)
            weights = self.make_var(name+'_W', shape=[dim, num_out])
            biases = self.make_var(name+'_b', [num_out])
            op = tf.nn.relu_layer if relu else tf.nn.xw_plus_b
            fc = op(feed_in, weights, biases, name=scope.name)
            return fc

    @layer
    def softmax(self, input, name):
        return tf.nn.softmax(input, name=name)

    @layer
    def batch_normalization(self, input, name, scale_offset=True, relu=False):
        # NOTE: Currently, only inference is supported
        with tf.variable_scope(name) as scope:
            shape = [input.get_shape()[-1]]
            if scale_offset:
                scale = self.make_var('scale', shape=shape)
                offset = self.make_var('offset', shape=shape)
            else:
                scale, offset = (None, None)
            output = tf.nn.batch_normalization(
                input,
                mean=self.make_var('mean', shape=shape),
                variance=self.make_var('variance', shape=shape),
                offset=offset,
                scale=scale,
                # TODO: This is the default Caffe batch norm eps
                # Get the actual eps from parameters
                variance_epsilon=1e-5,
                name=name)
            if relu:
                output = tf.nn.relu(output)
            return output

    @layer
    def dropout(self, input, keep_prob, name):
        keep = 1 - self.use_dropout + (self.use_dropout * keep_prob)
        return tf.nn.dropout(input, keep, name=name)
    @layer
    def cf_conv(self, input ,name,padding=DEFAULT_PADDING):
        template=input[0]
        detection=input[1]
        merge=tf.nn.conv2d(detection,template,strides=[1,1,1,1],padding=padding,name=name)
        return merge
    @layer
    def reshape(self,input, rate,name):
        shape=input.shape
        if rate==2:
            #output=tf.reshape(input,(shape[1],shape[2],int(shape[3]/(rate*self.k)),int(rate*self.k)),name=name)
            output=tf.reshape(input,(shape[1],shape[2],256,10),name=name)
        elif rate==4:
            output=tf.reshape(input,(shape[1],shape[2],256,20),name=name)
        else:
            raise KeyError('shape is error')
        return output
    @layer
    def reshape2(self,input,shape,name):
        return tf.reshape(input,shape,name=name)

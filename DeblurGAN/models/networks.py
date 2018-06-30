import torch
import torch.nn as nn
from torch.nn import init
import functools
from torch.autograd import Variable
import numpy as np
###############################################################################
# Functions
###############################################################################


def weights_init(m):
	classname = m.__class__.__name__
	if classname.find('Conv') != -1:
		m.weight.data.normal_(0.0, 0.02)
		if hasattr(m.bias, 'data'):
			m.bias.data.fill_(0)
	elif classname.find('BatchNorm2d') != -1:
		m.weight.data.normal_(1.0, 0.02)
		m.bias.data.fill_(0)


def get_norm_layer(norm_type='instance'):
	if norm_type == 'batch':
		norm_layer = functools.partial(nn.BatchNorm2d, affine=True)
	elif norm_type == 'instance':
		norm_layer = functools.partial(nn.InstanceNorm2d, affine=False, track_running_stats=True)
	else:
		raise NotImplementedError('normalization layer [%s] is not found' % norm_type)
	return norm_layer


def define_G(input_nc, output_nc, ngf, which_model_netG, norm='batch', use_dropout=False, gpu_ids=[], use_parallel = True, learn_residual = False):
	netG = None
	use_gpu = len(gpu_ids) > 0
	norm_layer = get_norm_layer(norm_type=norm)

	if use_gpu:
		assert(torch.cuda.is_available())

	if which_model_netG == 'resnet_9blocks':
		netG = ResnetGenerator(input_nc, output_nc, ngf, norm_layer=norm_layer, use_dropout=use_dropout, n_blocks=9, gpu_ids=gpu_ids, use_parallel=use_parallel, learn_residual = learn_residual)
	elif which_model_netG == 'resnet_6blocks':
		netG = ResnetGenerator(input_nc, output_nc, ngf, norm_layer=norm_layer, use_dropout=use_dropout, n_blocks=6, gpu_ids=gpu_ids, use_parallel=use_parallel, learn_residual = learn_residual)
	elif which_model_netG == 'unet_128':
		netG = UnetGenerator(input_nc, output_nc, 7, ngf, norm_layer=norm_layer, use_dropout=use_dropout, gpu_ids=gpu_ids, use_parallel=use_parallel, learn_residual = learn_residual)
	elif which_model_netG == 'unet_256':
		netG = UnetGenerator(input_nc, output_nc, 8, ngf, norm_layer=norm_layer, use_dropout=use_dropout, gpu_ids=gpu_ids, use_parallel=use_parallel, learn_residual = learn_residual)
	else:
		raise NotImplementedError('Generator model name [%s] is not recognized' % which_model_netG)
	if len(gpu_ids) > 0:
		netG.cuda(gpu_ids[0])
	netG.apply(weights_init)
	return netG


def define_D(input_nc, ndf, which_model_netD,
			 n_layers_D=3, norm='batch', use_sigmoid=False, gpu_ids=[], use_parallel = True):
	netD = None
	use_gpu = len(gpu_ids) > 0
	norm_layer = get_norm_layer(norm_type=norm)

	if use_gpu:
		assert(torch.cuda.is_available())
	if which_model_netD == 'basic':
		netD = NLayerDiscriminator(input_nc, ndf, n_layers=3, norm_layer=norm_layer, use_sigmoid=use_sigmoid, gpu_ids=gpu_ids, use_parallel=use_parallel)
	elif which_model_netD == 'n_layers':
		netD = NLayerDiscriminator(input_nc, ndf, n_layers_D, norm_layer=norm_layer, use_sigmoid=use_sigmoid, gpu_ids=gpu_ids, use_parallel=use_parallel)
	else:
		raise NotImplementedError('Discriminator model name [%s] is not recognized' %
								  which_model_netD)
	if use_gpu:
		netD.cuda(gpu_ids[0])
	netD.apply(weights_init)
	return netD


def print_network(net):
	num_params = 0
	for param in net.parameters():
		num_params += param.numel()
	print(net)
	print('Total number of parameters: %d' % num_params)


##############################################################################
# Classes
##############################################################################


# Defines the generator that consists of Resnet blocks between a few
# downsampling/upsampling operations.
# Code and idea originally from Justin Johnson's architecture.
# https://github.com/jcjohnson/fast-neural-style/
class ResnetGenerator(nn.Module):
	def __init__(self, input_nc, output_nc, ngf=64, norm_layer=nn.Batch
		Norm3d, use_dropout=False, n_blocks=6, gpu_ids=[], use_parallel = True, learn_residual = False, padding_type='replicate'):
		assert(n_blocks >= 0)
		super(ResnetGenerator, self).__init__()
		self.input_nc = input_nc
		self.output_nc = output_nc
		self.ngf = ngf
		self.gpu_ids = gpu_ids
		self.use_parallel = use_parallel
		self.learn_residual = learn_residual

		norm_layer = nn.BatchNorm3d
		# if type(norm_layer) == functools.partial:
		# 	use_bias = norm_layer.func == nn.InstanceNorm3d
		# else:
		# 	use_bias = norm_layer == nn.InstanceNorm3d
		use_bias = False
		model = [nn.ReplicationPad3d((1,1,1,1,0,0)),
				  nn.Conv3d(input_nc, ngf, kernel_size=(1,3,3), padding=0,
				 	   bias=use_bias),
				 nn.InstanceNorm3d(ngf),#norm_layer(ngf),
				 nn.ReLU(True)]

		n_downsampling = 2
		for i in range(n_downsampling):
			mult = 2**i
			model += [nn.Conv3d(ngf * mult, ngf * mult * 2, kernel_size=(1,3,3),
								stride=(1,2,2), padding=(0,1,1), bias=use_bias),
					  norm_layer(ngf * mult * 2),
					  nn.ReLU(True)]

		mult = 2**n_downsampling
		for i in range(n_blocks):
			model += [ResnetBlock(ngf * mult, padding_type=padding_type, norm_layer=norm_layer, use_dropout=use_dropout, use_bias=use_bias)]

		for i in range(n_downsampling):
			mult = 2**(n_downsampling - i)
			model += [nn.ConvTranspose3d(ngf * mult, int(ngf * mult / 2),
										 kernel_size=(1,3,3), stride=(1,2,2),
										 padding=(0,1,1), output_padding=(0,1,1),
										 bias=use_bias),
					  norm_layer(int(ngf * mult / 2)),
					  nn.ReLU(True)]
		model += [nn.ReplicationPad3d((3,3,3,3,0,0))]
		model += [nn.Conv3d(ngf, output_nc, kernel_size=(1,7,7), padding=0)]
		model += [nn.Tanh()]

		self.model = nn.Sequential(*model)

	def forward(self, input):
		if self.gpu_ids and isinstance(input.data, torch.cuda.FloatTensor) and self.use_parallel:
			output = nn.parallel.data_parallel(self.model, input, self.gpu_ids)
		else:
			output = self.model(input)
		if self.learn_residual:
			print input.shape, output.shape
			output = input + output
			output = torch.clamp(output,min = -1,max = 1)
		return output


# Define a resnet block
class ResnetBlock(nn.Module):
	def __init__(self, dim, padding_type, norm_layer, use_dropout, use_bias):
		super(ResnetBlock, self).__init__()
		self.conv_block = self.build_conv_block(dim, padding_type, norm_layer, use_dropout, use_bias)

	def build_conv_block(self, dim, padding_type, norm_layer, use_dropout, use_bias):
		conv_block = []
		p = 0
		if padding_type == 'reflect':
			conv_block += [nn.ReflectionPad3d(1)]
		elif padding_type == 'replicate':
			conv_block += [nn.ReplicationPad3d((1,1,1,1,0,0))]
		elif padding_type == 'zero':
			p = 1
		else:
			raise NotImplementedError('padding [%s] is not implemented' % padding_type)

		conv_block += [nn.Conv3d(dim, dim, kernel_size=(1,3,3), padding=(0,p,p), bias=use_bias),
					   norm_layer(dim),
					   nn.ReLU(True)]
		if use_dropout:
			conv_block += [nn.Dropout(0.5)]

		p = 0
		if padding_type == 'reflect':
			conv_block += [nn.ReflectionPad2d(1)]
		elif padding_type == 'replicate':
			conv_block += [nn.ReplicationPad3d((1,1,1,1,0,0))]
		elif padding_type == 'zero':
			p = 1
		else:
			raise NotImplementedError('padding [%s] is not implemented' % padding_type)
		conv_block += [nn.Conv3d(dim, dim, kernel_size=(1,3,3), padding=(0,p,p), bias=use_bias),
					   norm_layer(dim)]

		return nn.Sequential(*conv_block)

	def forward(self, x):
		out = x + self.conv_block(x)
		return out


# Defines the Unet generator.
# |num_downs|: number of downsamplings in UNet. For example,
# if |num_downs| == 7, image of size 128x128 will become of size 1x1
# at the bottleneck
class UnetGenerator(nn.Module):
	def __init__(self, input_nc, output_nc, num_downs, ngf=64,
				 norm_layer=nn.BatchNorm2d, use_dropout=False, gpu_ids=[], use_parallel = True, learn_residual = False):
		super(UnetGenerator, self).__init__()
		self.gpu_ids = gpu_ids
		self.use_parallel = use_parallel
		self.learn_residual = learn_residual
		# currently support only input_nc == output_nc
		assert(input_nc == output_nc)

		# construct unet structure
		unet_block = UnetSkipConnectionBlock(ngf * 8, ngf * 8, norm_layer=norm_layer, innermost=True)
		for i in range(num_downs - 5):
			unet_block = UnetSkipConnectionBlock(ngf * 8, ngf * 8, unet_block, norm_layer=norm_layer, use_dropout=use_dropout)
		unet_block = UnetSkipConnectionBlock(ngf * 4, ngf * 8, unet_block, norm_layer=norm_layer)
		unet_block = UnetSkipConnectionBlock(ngf * 2, ngf * 4, unet_block, norm_layer=norm_layer)
		unet_block = UnetSkipConnectionBlock(ngf, ngf * 2, unet_block, norm_layer=norm_layer)
		unet_block = UnetSkipConnectionBlock(output_nc, ngf, unet_block, outermost=True, norm_layer=norm_layer)

		self.model = unet_block

	def forward(self, input):
		if self.gpu_ids and isinstance(input.data, torch.cuda.FloatTensor) and self.use_parallel:
			output = nn.parallel.data_parallel(self.model, input, self.gpu_ids)
		else:
			output = self.model(input)
		if self.learn_residual:
			output = input + output
			output = torch.clamp(output,min = -1,max = 1)
		return output


# Defines the submodule with skip connection.
# X -------------------identity---------------------- X
#   |-- downsampling -- |submodule| -- upsampling --|
class UnetSkipConnectionBlock(nn.Module):
	def __init__(self, outer_nc, inner_nc,
				 submodule=None, outermost=False, innermost=False, norm_layer=nn.BatchNorm2d, use_dropout=False):
		super(UnetSkipConnectionBlock, self).__init__()
		self.outermost = outermost
		if type(norm_layer) == functools.partial:
			use_bias = norm_layer.func == nn.InstanceNorm2d
		else:
			use_bias = norm_layer == nn.InstanceNorm2d

		downconv = nn.Conv2d(outer_nc, inner_nc, kernel_size=4,
							 stride=2, padding=1, bias=use_bias)
		downrelu = nn.LeakyReLU(0.2, True)
		downnorm = norm_layer(inner_nc)
		uprelu = nn.ReLU(True)
		upnorm = norm_layer(outer_nc)

		if outermost:
			upconv = nn.ConvTranspose2d(inner_nc * 2, outer_nc,
										kernel_size=4, stride=2,
										padding=1)
			down = [downconv]
			up = [uprelu, upconv, nn.Tanh()]
			model = down + [submodule] + up
		elif innermost:
			upconv = nn.ConvTranspose2d(inner_nc, outer_nc,
										kernel_size=4, stride=2,
										padding=1, bias=use_bias)
			down = [downrelu, downconv]
			up = [uprelu, upconv, upnorm]
			model = down + up
		else:
			upconv = nn.ConvTranspose2d(inner_nc * 2, outer_nc,
										kernel_size=4, stride=2,
										padding=1, bias=use_bias)
			down = [downrelu, downconv, downnorm]
			up = [uprelu, upconv, upnorm]

			if use_dropout:
				model = down + [submodule] + up + [nn.Dropout(0.5)]
			else:
				model = down + [submodule] + up

		self.model = nn.Sequential(*model)

	def forward(self, x):
		if self.outermost:
			return self.model(x)
		else:
			return torch.cat([self.model(x), x], 1)


# Defines the PatchGAN discriminator with the specified arguments.
class NLayerDiscriminator(nn.Module):
	def __init__(self, input_nc, ndf=64, n_layers=3, norm_layer=nn.InstanceNorm3d, use_sigmoid=False, gpu_ids=[], use_parallel = True):
		super(NLayerDiscriminator, self).__init__()
		self.gpu_ids = gpu_ids
		self.use_parallel = use_parallel
		norm_layer = nn.InstanceNorm3d
		if type(norm_layer) == functools.partial:
			use_bias = norm_layer.func == nn.InstanceNorm3d
		else:
			use_bias = norm_layer == nn.InstanceNorm3d
		kw = 4
		padw = int(np.ceil((kw-1)/2))
		sequence = [
			nn.Conv3d(input_nc, ndf, kernel_size=(1,kw,kw), stride=(1,2,2), padding=(0,padw,padw)),
			nn.LeakyReLU(0.2, True)
		]

		nf_mult = 1
		nf_mult_prev = 1
		for n in range(1, n_layers):
			nf_mult_prev = nf_mult
			nf_mult = min(2**n, 8)
			sequence += [
				nn.Conv3d(ndf * nf_mult_prev, ndf * nf_mult,
						  kernel_size=(1,kw,kw), stride=(1,2,2), padding=(0,padw,padw), bias=use_bias),
				norm_layer(ndf * nf_mult),
				nn.LeakyReLU(0.2, True)
			]

		nf_mult_prev = nf_mult
		nf_mult = min(2**n_layers, 8)
		sequence += [
			nn.Conv3d(ndf * nf_mult_prev, ndf * nf_mult,
					  kernel_size=(1,kw,kw), stride=1, padding=(0,padw,padw), bias=use_bias),
			norm_layer(ndf * nf_mult),
			nn.LeakyReLU(0.2, True)
		]

		sequence += [nn.Conv3d(ndf * nf_mult, 1, kernel_size=(1,kw,kw), stride=1, padding=(0,padw,padw)]

		if use_sigmoid:
			sequence += [nn.Sigmoid()]

		self.model = nn.Sequential(*sequence)

	def forward(self, input):
		if len(self.gpu_ids) and isinstance(input.data, torch.cuda.FloatTensor) and self.use_parallel:
			return nn.parallel.data_parallel(self.model, input, self.gpu_ids)
		else:
			return self.model(input)

## need scikit-tensor-py3 package...
## copied it here by hand, with minor modifications to suit current scipy version

from sktensor.tucker import hooi, hosvd
from sktensor import dtensor

import numpy as np
from numpy.linalg import pinv, matrix_rank
from numpy import transpose, diag

from .randomgen import RandGenerator


def _append_random_NxN_fullrank(colvec: np.ndarray, randomgen: RandGenerator | None = None) -> np.ndarray:
	"""Create a matrix by appending random full-rank columns to a vector.

	Parameters
	----------
	colvec : np.ndarray
		Input column vector of shape (n, 1)
	randomgen : RandGenerator | None, optional
		Random number generator instance, by default None

	Returns
	-------
	np.ndarray
		Matrix of shape (n, n+1) where first column is colvec and remaining
		columns contain random values with same range as colvec

	Notes
	-----
	The appended random matrix portion is guaranteed to be full rank.
	"""
	dim_R = colvec.shape[0]
	minval = np.min(colvec)
	maxval = np.max(colvec)

	if randomgen is None:
		randomgen = RandGenerator()

	num_tries = 0
	while True:
		num_tries += 1

		R = randomgen.uniform(size=(dim_R, dim_R)) * (maxval - minval) + minval
		rank = np.linalg.matrix_rank(R)

		if rank == dim_R:
			break

		if num_tries > 100:
			num_tries = 0
			maxval += 1

	return np.concatenate((colvec, R), 1)

def _append_random_SameTensor(tensor: np.ndarray, randomgen: RandGenerator | None = None) -> np.ndarray:
	"""Extend tensor by appending random values along last dimension.

	Parameters
	----------
	tensor : np.ndarray
		Input tensor of shape (1, n1, n2, ..., nx)
	randomgen : RandGenerator | None, optional
		Random number generator instance, by default None

	Returns
	-------
	np.ndarray
		Extended tensor of shape (1, n1, n2, ..., 2*nx) where appended values
		are randomly distributed within same range as input tensor
	"""
	minval = np.min(tensor)
	maxval = np.max(tensor)

	if randomgen is None:
		randomgen = RandGenerator()

	return np.concatenate(
		(tensor, randomgen.uniform(size=tensor.shape) * (maxval - minval) + minval),
		len(tensor.shape)-1
	)

def _append_zero_SameTensor(tensor: np.ndarray) -> np.ndarray:
	"""Extend tensor by appending zeros along last dimension.

	Parameters
	----------
	tensor : np.ndarray
		Input tensor of shape (1, n1, n2, ..., nx)

	Returns
	-------
	np.ndarray
		Extended tensor of shape (1, n1, n2, ..., 2*nx) where appended values are zero
	"""
	return np.concatenate(
		(tensor, np.zeros(tensor.shape)), ## here you need a tuple, wheras with random.rand you need plain values... just great
		len(tensor.shape)-1
	)

def _invert_values_secondhalf_lastdim(tensor: np.ndarray) -> np.ndarray:
	"""Invert values in second half of last dimension.

	Parameters
	----------
	tensor : np.ndarray
		Input tensor

	Returns
	-------
	np.ndarray
		Copy of input tensor with values in second half of last dimension multiplied by -1
	"""
	tensor_copy = tensor.copy()
	lastdim = tensor.shape[-1]
	lastdim_half = lastdim // 2

	tensor_copy[..., lastdim_half:] *= -1
	return tensor_copy


def _append_eye(matrix: np.ndarray) -> np.ndarray:
	"""Append identity matrix to input matrix.

	Parameters
	----------
	matrix : np.ndarray
		Input matrix of shape (n, r)

	Returns
	-------
	np.ndarray
		Extended matrix of shape (n, r+n) with identity matrix appended
	"""
	return np.concatenate((matrix, np.eye(matrix.shape[0], dtype=float)), 1)

def sdm_init(x_tensor: np.ndarray, sh_pos_part_2: np.ndarray | None = None,
             randomgen: RandGenerator | None = None) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
	"""Initialize SDM parameters including core tensors and weight matrices.

	Parameters
	----------
	x_tensor : np.ndarray
		Input tensor to be processed
	sh_pos_part_2 : np.ndarray | None, optional
		Extended part of Sh for testing (subtensor K in JACIII publication), by default None
	randomgen : RandGenerator | None, optional
		Random number generator instance, by default None

	Returns
	-------
	tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray]
		Tuple containing:
		- weight_matrices: List of weight matrices
		- zero_core_tensor: Core tensor with zero extension
		- max_core_tensor: Core tensor with positive random extension
		- min_core_tensor: Core tensor with negative extension
	"""

	Sh, Uhlist = hooi(dtensor(x_tensor), [1] + list(x_tensor.shape)[1:], init='nvecs')
	U1firstcol = Uhlist[0][:,0].reshape(-1,1)
	Uhlist[0] = _append_random_NxN_fullrank(U1firstcol, randomgen)
	Uhlist[-1] = _append_eye(Uhlist[-1])
	R = Uhlist[0][:, 1:]

	if sh_pos_part_2 is None:
		Sh_pos = _append_random_SameTensor(Sh, randomgen)
	else:
		Sh_pos = np.concatenate((Sh, sh_pos_part_2), len(Sh.shape)-1)

	Sh_zero = _append_zero_SameTensor(Sh)
	Sh_neg = _invert_values_secondhalf_lastdim(Sh_pos)
	D_pos = dtensor(x_tensor)
	for dim in range(1, len(x_tensor.shape)):
		D_pos = D_pos.ttm(pinv(Uhlist[dim]), dim)
	D_possub = dtensor(Sh_pos).ttm(U1firstcol, 0)
	D_pos = (D_pos - D_possub).ttm(pinv(R), 0)

	S_pos = np.concatenate((Sh_pos, np.array(D_pos, dtype=float)), 0)

	D_zero = dtensor(x_tensor)
	for dim in range(1, len(x_tensor.shape)):
		D_zero = D_zero.ttm(pinv(Uhlist[dim]), dim)
	D_zerosub = dtensor(Sh_zero).ttm(U1firstcol, 0)
	D_zero = (D_zero - D_zerosub).ttm(pinv(R), 0)

	S_zero = np.concatenate((Sh_zero, np.array(D_zero, dtype=float)), 0)

	D_neg = dtensor(x_tensor)
	for dim in range(1, len(x_tensor.shape)):
		D_neg = D_neg.ttm(pinv(Uhlist[dim]), dim)
	D_negsub = dtensor(Sh_neg).ttm(U1firstcol, 0)
	D_neg = (D_neg - D_negsub).ttm(pinv(R), 0)

	S_neg = np.concatenate((Sh_neg, np.array(D_neg, dtype=float)), 0)

	return (Uhlist, S_zero, S_pos, S_neg)


def _snake_combo_generator(path_len: int, phase_shift: float = 0.0):
	"""Generate interpolation coefficients for tensor exploration.

	Parameters
	----------
	path_len : int
		Length of each path segment
	phase_shift : float, optional
		Phase shift value between 0.0 and 1.0, by default 0.0

	Yields
	------
	list[float]
		Three interpolation coefficients [min_factor, med_factor, max_factor]

	Notes
	-----
	Generates coefficients for interpolating between minimum, median, and maximum
	values in a snake-like pattern:
	1. Median to maximum
	2. Maximum to median
	3. Median to minimum
	4. Minimum to median
	"""
	if not 0.0 <= phase_shift <= 1.0:
		raise ValueError("phase_shift must be between 0.0 and 1.0")
	if path_len < 1:
		raise ValueError("path_len must be positive")
	
	init_combo = [0, 1.0, 0]
	incr = 1.0 / path_len
	phase_shift_remainder = int(phase_shift * 4 * path_len)
	phase_shift_orig = phase_shift_remainder

	for _ in range(path_len):
		init_combo[1], init_combo[2] = init_combo[1] - incr, init_combo[2] + incr
		
		if phase_shift_remainder < 1:
			yield [round(combo,2) for combo in init_combo]
		else:
			phase_shift_remainder -= 1
	for _ in range(path_len):
		init_combo[1], init_combo[2] = init_combo[1] + incr, init_combo[2] - incr

		if phase_shift_remainder < 1:
			yield [round(combo,2) for combo in init_combo]
		else:
			phase_shift_remainder -= 1

	init_combo = [0, 1.0, 0]
	for _ in range(path_len):
		init_combo[0], init_combo[1] = init_combo[0] + incr, init_combo[1] - incr
		
		if phase_shift_remainder < 1:
			yield [round(combo,2) for combo in init_combo]
		else:
			phase_shift_remainder -= 1
	for _ in range(path_len):
		init_combo[0], init_combo[1] = init_combo[0] - incr, init_combo[1] + incr
		
		if phase_shift_remainder < 1:
			yield [round(combo,2) for combo in init_combo]
		else:
			phase_shift_remainder -= 1

	if phase_shift_orig > 0:
		init_combo = [0, 1.0, 0]
		phase_shift_remainder = phase_shift_orig
		for _ in range(path_len):
			init_combo[1], init_combo[2] = init_combo[1] - incr, init_combo[2] + incr
			
			if phase_shift_remainder > 0:
				yield [round(combo,2) for combo in init_combo]
				phase_shift_remainder -= 1
			else:
				return
		for _ in range(path_len):
			init_combo[1], init_combo[2] = init_combo[1] + incr, init_combo[2] - incr
			
			if phase_shift_remainder > 0:
				yield [round(combo,2) for combo in init_combo]
				phase_shift_remainder -= 1
			else:
				return
		init_combo = [0, 1.0, 0]
		for _ in range(path_len):
			init_combo[0], init_combo[1] = init_combo[0] + incr, init_combo[1] - incr

			if phase_shift_remainder > 0:
				yield [round(combo,2) for combo in init_combo]
				phase_shift_remainder -= 1
			else:
				return
		for _ in range(path_len):
			init_combo[0], init_combo[1] = init_combo[0] - incr, init_combo[1] + incr

			if phase_shift_remainder > 0:
				yield [round(combo,2) for combo in init_combo]
				phase_shift_remainder -= 1
			else:
				return
	return


##def get_exploration_params(x_tensor):
def create_sdm_state(x_tensor: np.ndarray, randomgen: RandGenerator | None = None) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
	"""Create reusable SDM state parameters.

	Parameters
	----------
	x_tensor : np.ndarray
		Input tensor to be processed
	randomgen : RandGenerator | None, optional
		Random number generator instance, by default None

	Returns
	-------
	tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray]
		SDM state parameters (same as sdm_init return value)
	"""
	(Uhlist, Sh0, Shmax, Shmin) = sdm_init(x_tensor, randomgen=randomgen)
	return (Uhlist, Sh0, Shmax, Shmin)


def explore_tensor(x_tensor: np.ndarray, path_len: int = 20, step_sz: float = 0.01,
                  sdm_state: tuple | None = None, phase_shift: float = 0.0):
	"""Generate sequence of tensors exploring the parameter space.

	Parameters
	----------
	x_tensor : np.ndarray
		Input tensor to explore
	path_len : int, optional
		Length of each path segment, by default 20
	step_sz : float, optional
		Step size for parameter updates, by default 0.01
	sdm_state : tuple | None, optional
		Pre-computed SDM state parameters, by default None
	phase_shift : float, optional
		Phase shift for exploration pattern, by default 0.0

	Yields
	------
	np.ndarray
		Transformed tensor at each exploration step
	"""
	if sdm_state is None:
		(Uhlist, Sh0, Shmax, Shmin) = sdm_init(x_tensor)
	else:
		(Uhlist, Sh0, Shmax, Shmin) = sdm_state

	path_factors_generator = _snake_combo_generator(path_len, phase_shift)

	Uhlist0 = [elem.copy() for elem in Uhlist]

	while True:
		try:
			factors = next(path_factors_generator)
			Uhlist0[0][:, 0] += step_sz
			S = factors[0] * Shmin + factors[1] * Sh0 + factors[2] * Shmax
			result = dtensor(S)
			for dim in range(len(Uhlist0)): ## S has same no. of dimensions as x_tensor?
				result = result.ttm(Uhlist0[dim], dim)
			## TODO: returning the second part in each case is redundant:
			yield result
		except StopIteration:
			break

def explore_tensor_and_do(x_tensor, func_to_do_in_each_iter, func_to_do_at_end, path_len=20, step_sz=0.01):
	exploration = explore_tensor(x_tensor, path_len, step_sz)

	inx = 0
	while True:
		try:
			e = next(exploration)
			func_to_do_in_each_iter(e, x_tensor, inx)
			inx += 1
		except StopIteration:
			break

	func_to_do_at_end(x_tensor)


import numpy as np
from dataclasses import dataclass, field
import csv

@dataclass(frozen=True)
class RandGenerator:
	seed: int = None
	generator: np.random.Generator = field(init=False)

	def __post_init__(self):
		object.__setattr__(self, "generator", np.random.default_rng(self.seed))

	def _get_state(self):
		return self.generator.bit_generator.state

	def _set_state(self, state):
		self.generator.bit_generator.state = state

	def copy(self):
		newgen = RandGenerator(self.seed)
		newgen._set_state(self._get_state())
		return newgen

	def sample_float_01(self):
		return self.generator.random()

	def uniform(self, size):
		return self.generator.uniform(size=size)

	def sample_n_from_k(self, n, k):
		return self.generator.permutation(k)[:n]

	def shuffle_k(self, k):
		return self.generator.permutation(k)

	def partition(self, N, minsz, maxsz, verbose=False):
		"""
		Create a random partitioning of numbers 0..N-1 such that each partition has between minsz and maxsz points.
		"""
		##print(f"partitioning {N} w/ {minsz} and {maxsz}")

		if minsz == 0:
			if maxsz < 3:
				raise Exception(f"minsz = 0 and maxsz < 3, so please use a larger dataset")
			else:
				minsz = 2

		partition_sizes = [N]
		splitrange = maxsz - minsz + 1

		while True:
			if all(elem >= minsz and elem <= maxsz for elem in partition_sizes):
				break
		
			deleteinxes, inserts = [], []

			for inx, elem in enumerate(partition_sizes):
				if elem >= minsz and elem <= maxsz:
					continue

				deleteinxes.append(inx)

				breakoff_inxes = self.shuffle_k(splitrange)

				potential_breakoff_as = [i for i in range(minsz, minsz+splitrange)]

				breakoff_as = [potential_breakoff_as[breakoff_inx] for breakoff_inx in breakoff_inxes]
				breakoff_bs = [elem - a for a in breakoff_as]

				did_find_breakoff = False
				for breakoff_inx in range(splitrange):
					a = breakoff_as[breakoff_inx]
					b = breakoff_bs[breakoff_inx]

					a_mod_min = a % minsz
					b_mod_min = b % minsz

					a_mod_max = a % maxsz
					b_mod_max = b % maxsz

					a_good = (a >= minsz) and (a <= maxsz or (a_mod_min == 0) or (a_mod_min < splitrange) or (a_mod_max == 0))
					b_good = (b >= minsz) and (b <= maxsz or (b_mod_min == 0) or (b_mod_min < splitrange) or (b_mod_max == 0))

					if a_good and b_good:
						inserts.append(a)
						inserts.append(b)
						did_find_breakoff = True
						break
					else:
						if verbose:
							print(f"\tindadmissible split: {elem} -> {a}, {b}")
			
				if not did_find_breakoff:
					return self.partition(
						N,
						int(minsz * 0.9),
						min(N, int(maxsz * 1.1))
					)
					##raise Exception(f"no admissible split found for {N}/{minsz}/{maxsz} when it came to splitting {elem}")

				
			deleteinxes.reverse()
			for inx in deleteinxes:
				partition_sizes.pop(inx)

			partition_sizes = partition_sizes + inserts
			if verbose:
				print(f"partitions: {partition_sizes}")

		permutation = self.shuffle_k(N)
		partitions = []
		perm_inx = 0
		for sz in partition_sizes:
			partitions.append(permutation[perm_inx:perm_inx+sz])
			perm_inx += sz

		return partitions

	def bag(self, N, minsz, maxsz, totsz, verbose=False):
		"""
		Create a set of random bags from numbers 0..N-1 such that each bag has between minsz and maxsz points, and
		the sum of the sizes of all bags does not exceed totsz.
		"""

		## use partition() method just to get partition sizes
		## note: we won't ever use the partitions, just their length / size
		## so the first argument can be totsz, since that's how many points we will need
		partitions = self.partition(totsz, minsz, maxsz)

		bags = []
		numitems = 0

		for p in partitions:
			plen = len(p)
			permutation = self.shuffle_k(N)
			bags.append([pi for pi in permutation[:plen]])

		return bags


def serialize_rng_state(rng_state: dict, filepath: str):
    """Serialize the RNG state dictionary to a CSV file."""
    with open(filepath, mode='w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['key', 'subkey', 'value'])  # Header

        for key, value in rng_state.items():
            if isinstance(value, dict):
                for subkey, subvalue in value.items():
                    writer.writerow([key, subkey, subvalue])
            else:
                writer.writerow([key, '', value])


def deserialize_rng_state(filepath: str) -> dict:
    """Deserialize the RNG state dictionary from a CSV file."""
    state = {}
    with open(filepath, mode='r', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            key, subkey, value = row['key'], row['subkey'], row['value']

            # Handle nested dictionaries
            if subkey:
                if key not in state:
                    state[key] = {}
                state[key][subkey] = int(value)
            else:
                try:
                    state[key] = int(value)
                except ValueError:
                    state[key] = str(value)

    return state
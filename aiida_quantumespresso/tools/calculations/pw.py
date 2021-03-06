# -*- coding: utf-8 -*-
"""Tools for nodes created by running the `PwCalculation` class."""
from __future__ import absolute_import
from aiida.common import exceptions
from aiida.tools.calculations import CalculationTools


class PwCalculationTools(CalculationTools):
    """Calculation tools for `PwCalulation`.

    Methods implemented here are available on any `CalcJobNode` produced by the `PwCalculation class through the
    `tools` attribute.
    """

    # pylint: disable=too-few-public-methods

    def get_scf_accuracy(self, index=0):
        """Return the array of SCF accuracy values for a given SCF cycle.

        :param index: the zero-based index of the desired SCF cycle
        :return: a list of SCF accuracy values of a certain SCF cycle.
        :raises ValueError: if the node does not have the `output_trajectory` output
        :raises ValueError: if `output_trajectory` does not have the `scf_accuracy` or `scf_accuracy_index` arrays
        :raises IndexError: if the `index` is out of range
        """
        try:
            trajectory = self._node.outputs.output_trajectory
        except exceptions.NotExistent:
            raise ValueError('{} does not have the `output_trajectory` output node'.format(self._node))

        try:
            scf_accuracy = trajectory.get_array('scf_accuracy')
        except KeyError:
            raise ValueError('{} does not contain the required `scf_accuracy` array'.format(trajectory))

        try:
            scf_accuracy_index = trajectory.get_array('scf_accuracy_index')
        except KeyError:
            raise ValueError('{} does not contain the required `scf_accuracy_index` array'.format(trajectory))

        number_of_frames = len(scf_accuracy_index) - 1

        if index < -number_of_frames or index >= number_of_frames:
            raise IndexError('invalid index {}, must be between 0 and {}'.format(index, number_of_frames - 1))

        if index == -1:
            return scf_accuracy[-1:]

        return scf_accuracy[scf_accuracy_index[index]:scf_accuracy_index[index + 1]]

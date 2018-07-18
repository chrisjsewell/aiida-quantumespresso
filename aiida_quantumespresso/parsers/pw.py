# -*- coding: utf-8 -*-
import numpy
from aiida.common.exceptions import UniquenessError
from aiida.orm.data.array.bands import BandsData
from aiida.orm.data.array.kpoints import KpointsData
from aiida.orm.data.parameter import ParameterData
from aiida.parsers.parser import Parser
from aiida_quantumespresso.calculations.pw import PwCalculation
from aiida_quantumespresso.parsers import convert_qe2aiida_structure
from aiida_quantumespresso.parsers.raw_parser_pw import parse_raw_output, QEOutputParsingError
from aiida_quantumespresso.utils.linalg import are_matrices_equal


class PwParser(Parser):
    """
    The Parser implementation for the PwCalculation JobCalculation class
    """

    _setting_key = 'parser_options'

    def __init__(self, calc):
        """
        Initialize the Parser for a PwCalculation

        :param calculation: instance of the PwCalculation
        """
        self._possible_symmetries = self._get_qe_symmetry_list()

        if not isinstance(calc, PwCalculation):
            raise QEOutputParsingError('Input calc must be a PwCalculation')

        super(PwParser, self).__init__(calc)

    def parse_with_retrieved(self, retrieved):
        """
        Parse the output nodes for a PwCalculations from a dictionary of retrieved nodes.
        Two nodes that are expected are the default 'retrieved' FolderData node which will
        store the retrieved files permanently in the repository. The second required node
        is the unstored FolderData node with the temporary retrieved files, which should
        be passed under the key 'retrieved_temporary_folder_key' of the Parser class.

        :param retrieved: a dictionary of retrieved nodes
        """
        import os

        successful = True

        # Load the input dictionary
        parameters = self._calc.inp.parameters.get_dict()

        # Look for optional settings input node and potential 'parser_options' dictionary within it
        try:
            settings = self._calc.inp.settings.get_dict()
            parser_opts = settings[self.get_parser_settings_key()]
        except (AttributeError, KeyError):
            settings = {}
            parser_opts = {}

        # Check that the retrieved folder is there
        try:
            out_folder = retrieved[self._calc._get_linkname_retrieved()]
        except KeyError:
            self.logger.error("No retrieved folder found")
            return False, ()

        # Verify that the retrieved_temporary_folder is within the arguments if temporary files were specified
        if self._calc._get_retrieve_temporary_list():
            try:
                temporary_folder = retrieved[self.retrieved_temporary_folder_key]
                dir_with_bands = temporary_folder
            except KeyError:
                self.logger.error('the {} was not passed as an argument'.format(self.retrieved_temporary_folder_key))
                return False, ()
        else:
            dir_with_bands = None

        list_of_files = out_folder.get_folder_list()

        # The stdout is required for parsing
        if self._calc._OUTPUT_FILE_NAME not in list_of_files:
            self.logger.error("The standard output file '{}' was not found but is required".format(self._calc._OUTPUT_FILE_NAME))
            return False, ()
        else:
            out_file = os.path.join(out_folder.get_abs_path('.'), self._calc._OUTPUT_FILE_NAME)

        # The xml file is required for successful parsing, but if it does not exist, we still try to parse the out file
        xml_files = [xml_file for xml_file in self._calc.xml_files if xml_file in list_of_files]
        xml_file = None

        if xml_files is None:
            successful = False
            self.logger.error('no XML output files found, which is required for parsing')
        elif len(xml_files) > 1:
            successful = False
            self.logger.error('more than one XML file retrieved, which should never happen')
        else:
            xml_file = os.path.join(out_folder.get_abs_path('.'), xml_files[0])

        # Call the raw parsing function
        parsing_args = [out_file, parameters, parser_opts, xml_file, dir_with_bands]
        out_dict, trajectory_data, structure_data, bands_data, raw_successful = parse_raw_output(*parsing_args)

        # If calculation was not considered failed already, use the new value
        successful = raw_successful if successful else successful

        # If the parser option 'all_symmetries' is not set to True, we reduce the raw parsed symmetries to safe space
        all_symmetries = parser_opts.get('all_symmetries', False)

        if not all_symmetries and 'cell' in structure_data:

            # In the standard output, each symmetry operation print two rotation matrices:
            #
            # * S_cryst^T: matrix in crystal coordinates, transposed
            # * S_cart: matrix in cartesian coordinates,
            #
            # The XML files only print one matrix:
            #
            # * S_cryst: matrix in crystal coordinates
            #
            # The raw parsed symmetry information from the XML is large and will load the database heavily if stored as
            # is for each calculation. Instead, we will map these dictionaries onto a static dictionary of rotation
            # matrices generated by the _get_qe_symmetry_list static method. This dictionary will return the rotation
            # matrices in cartesian coordinates, i.e. S_cart. In order to compare the raw matrices from the XML to these
            # static matrices we have to convert S_cryst into S_cart. We derive here how that is done:
            #
            #   S_cryst * v_cryst = v_cryst'
            #
            # where v_cryst' is the rotated vector v_cryst under S_cryst
            # We define `cell` where cell vectors are rows. Converting a vector from crystal to cartesian
            # coordinates is defined as:
            #
            #   cell^T * v_cryst = v_cart
            #
            # The inverse of this operation is defined as
            #
            #   v_cryst = cell^Tinv * v_cart
            #
            # Replacing the last equation into the first we find:
            #
            #   S_cryst * cell^Tinv * v_cart = cell^Tinv * v_cart'
            #
            # Multiply on the left with cell^T gives:
            #
            #   cell^T * S_cryst * cell^Tinv * v_cart = v_cart'
            #
            # which can be rewritten as:
            #
            #   S_cart * v_cart = v_cart'
            #
            # where:
            #
            #   S_cart = cell^T * S_cryst * cell^Tinv
            #
            # We compute here the transpose and its inverse of the structure cell basis, which is needed to transform
            # the parsed rotation matrices, which are in crystal coordinates, to cartesian coordinates, which are the
            # matrices that are returned by the _get_qe_symmetry_list staticmethod
            cell = structure_data['cell']['lattice_vectors']
            cell_T = numpy.transpose(cell)
            cell_Tinv = numpy.linalg.inv(cell_T)

            try:
                if 'symmetries' in out_dict.keys():
                    old_symmetries = out_dict['symmetries']
                    new_symmetries = []
                    for this_sym in old_symmetries:
                        name = this_sym['name'].strip()
                        for i, this in enumerate(self._possible_symmetries):
                            # Since we do an exact comparison we strip the string name from whitespace
                            # and as soon as it is matched, we break to prevent it from matching another
                            if name == this['name'].strip():
                                index = i
                                break
                        else:
                            index = None
                            self.logger.error('Symmetry {} not found'.format(name))

                        new_dict = {}
                        if index is not None:
                            # The raw parsed rotation matrix is in crystal coordinates, whereas the mapped rotation
                            # in self._possible_symmetries is in cartesian coordinates. To allow them to be compared
                            # to make sure we matched the correct rotation symmetry, we first convert the parsed matrix
                            # to cartesian coordinates. For explanation of the method, see comment above.
                            rotation_cryst = this_sym['rotation']
                            rotation_cart_new = self._possible_symmetries[index]['matrix']
                            rotation_cart_old = numpy.dot(cell_T, numpy.dot(rotation_cryst, cell_Tinv))

                            inversion = self._possible_symmetries[index]['inversion']
                            if not are_matrices_equal(rotation_cart_old, rotation_cart_new, swap_sign_matrix_b=inversion):
                                self.logger.error('Mapped rotation matrix {} does not match the original rotation {}'
                                    .format(rotation_cart_new, rotation_cart_old))
                                new_dict['all_symmetries'] = this_sym
                            else:
                                # Note: here I lose the information about equivalent ions and fractional_translation.
                                new_dict['t_rev'] = this_sym['t_rev']
                                new_dict['symmetry_number'] = index
                        else:
                            new_dict['all_symmetries'] = this_sym

                        new_symmetries.append(new_dict)

                    out_dict['symmetries'] = new_symmetries  # and overwrite the old one
            except KeyError:  # no symmetries were parsed (failed case, likely)
                self.logger.error("No symmetries were found in output")

        new_nodes_list = []

        # I eventually save the new structure. structure_data is unnecessary after this
        in_struc = self._calc.get_inputs_dict()['structure']
        type_calc = parameters['CONTROL']['calculation']
        struc = in_struc
        if type_calc in ['relax', 'vc-relax', 'md', 'vc-md']:
            if 'cell' in structure_data.keys():
                struc = convert_qe2aiida_structure(structure_data, input_structure=in_struc)
                new_nodes_list.append((self.get_linkname_outstructure(), struc))

        k_points_list = trajectory_data.pop('k_points', None)
        k_points_weights_list = trajectory_data.pop('k_points_weights', None)

        if k_points_list is not None:

            # Build the kpoints object
            if out_dict['k_points_units'] not in ['2 pi / Angstrom']:
                raise QEOutputParsingError('Error in kpoints units (should be cartesian)')

            kpoints_from_output = KpointsData()
            kpoints_from_output.set_cell_from_structure(struc)
            kpoints_from_output.set_kpoints(k_points_list, cartesian=True, weights=k_points_weights_list)
            kpoints_from_input = self._calc.inp.kpoints

            if not bands_data:
                try:
                    kpoints_from_input.get_kpoints()
                except AttributeError:
                    new_nodes_list += [(self.get_linkname_out_kpoints(), kpoints_from_output)]

            # Converting bands into a BandsData object (including the kpoints)
            if bands_data:
                kpoints_for_bands = kpoints_from_output

                try:
                    kpoints_from_input.get_kpoints()
                    kpoints_for_bands.labels = kpoints_from_input.labels
                except (AttributeError, ValueError, TypeError):
                    # AttributeError: no list of kpoints in input
                    # ValueError: labels from input do not match the output
                    #      list of kpoints (some kpoints are missing)
                    # TypeError: labels are not set, so kpoints_from_input.labels=None
                    pass

                # Get the bands occupations and correct the occupations of QE:
                # If it computes only one component, it occupies it with half number of electrons
                try:
                    bands_data['occupations'][1]
                    the_occupations = bands_data['occupations']
                except IndexError:
                    the_occupations = 2. * numpy.array(bands_data['occupations'][0])

                try:
                    bands_data['bands'][1]
                    bands_energies = bands_data['bands']
                except IndexError:
                    bands_energies = bands_data['bands'][0]

                the_bands_data = BandsData()
                the_bands_data.set_kpointsdata(kpoints_for_bands)
                the_bands_data.set_bands(bands_energies,
                                         units=bands_data['bands_units'],
                                         occupations=the_occupations)

                new_nodes_list += [('output_band', the_bands_data)]
                out_dict['linknames_band'] = ['output_band']

        # Separate the atomic_occupations dictionary in its own node if it is present
        atomic_occupations = out_dict.get('atomic_occupations', {})
        if atomic_occupations:
            out_dict.pop('atomic_occupations')
            atomic_occupations_node = ParameterData(dict=atomic_occupations)
            new_nodes_list.append(('output_atomic_occupations', atomic_occupations_node))

        output_params = ParameterData(dict=out_dict)
        new_nodes_list.append((self.get_linkname_outparams(), output_params))

        if trajectory_data:
            from aiida.orm.data.array.trajectory import TrajectoryData
            from aiida.orm.data.array import ArrayData
            try:
                positions = numpy.array(trajectory_data.pop('atomic_positions_relax'))
                try:
                    cells = numpy.array(trajectory_data.pop('lattice_vectors_relax'))
                    # if KeyError, the MD was at fixed cell
                except KeyError:
                    cells = numpy.array([in_struc.cell] * len(positions))

                symbols = numpy.array([str(i.kind_name) for i in in_struc.sites])
                stepids = numpy.arange(len(positions))  # a growing integer per step
                # I will insert time parsing when they fix their issues about time
                # printing (logic is broken if restart is on)

                traj = TrajectoryData()
                traj.set_trajectory(
                    stepids=stepids,
                    cells=cells,
                    symbols=symbols,
                    positions=positions,
                )
                for x in trajectory_data.iteritems():
                    traj.set_array(x[0], numpy.array(x[1]))
                new_nodes_list.append((self.get_linkname_outtrajectory(), traj))

            except KeyError:
                # forces, atomic charges and atomic mag. moments, in scf calculation (when outputed)
                arraydata = ArrayData()
                for x in trajectory_data.iteritems():
                    arraydata.set_array(x[0], numpy.array(x[1]))
                new_nodes_list.append((self.get_linkname_outarray(), arraydata))

        return successful, new_nodes_list

    def get_parser_settings_key(self):
        """
        Return the name of the key to be used in the calculation settings, that
        contains the dictionary with the parser_options
        """
        return 'parser_options'

    def get_linkname_outstructure(self):
        """
        Returns the name of the link to the output_structure
        Node exists if positions or cell changed.
        """
        return 'output_structure'

    def get_linkname_outtrajectory(self):
        """
        Returns the name of the link to the output_trajectory.
        Node exists in case of calculation='md', 'vc-md', 'relax', 'vc-relax'
        """
        return 'output_trajectory'

    def get_linkname_outarray(self):
        """
        Returns the name of the link to the output_array
        Node may exist in case of calculation='scf'
        """
        return 'output_array'

    def get_linkname_out_kpoints(self):
        """
        Returns the name of the link to the output_kpoints
        Node exists if cell has changed and no bands are stored.
        """
        return 'output_kpoints'

    def get_extended_symmetries(self):
        """
        Return the extended dictionary of symmetries.
        """
        data = self._calc.get_outputs(node_type=ParameterData, also_labels=True)
        all_data = [i[1] for i in data if i[0] == self.get_linkname_outparams()]
        if len(all_data) > 1:
            raise UniquenessError('More than one output parameterdata found.')
        elif not all_data:
            return []
        else:
            compact_list = all_data[0].get_dict()['symmetries']  # rimetti lo zero
            new_list = []
            # copy what wasn't compact
            for element in compact_list:
                new_dict = {}
                for keys in ['t_rev', 'equivalent_ions', 'fractional_translation']:
                    try:
                        new_dict[keys] = element[keys]
                    except KeyError:
                        pass
                # expand the rest
                new_dict['name'] = self._possible_symmetries[element['symmetry_number']]['name']
                new_dict['rotation'] = self._possible_symmetries[element['symmetry_number']]['matrix']
                new_dict['inversion'] = self._possible_symmetries[element['symmetry_number']]['inversion']
                new_list.append(new_dict)
            return new_list

    @staticmethod
    def _get_qe_symmetry_list():
        """
        Hard coded names and rotation matrices + inversion from QE v 5.0.2
        Function for Parser class usage only.

        :return: a list of dictionaries, each containing name (string),
            inversion (boolean) and matrix (list of lists)
        """
        sin3 = 0.866025403784438597
        cos3 = 0.5
        msin3 = -0.866025403784438597
        mcos3 = -0.5

        # 32 rotations that are checked + inversion taken from symm_base.f90 from the QE source code
        # They are in Fortran format and therefore transposed with respect to the default python format
        transposed_matrices_cartesian = [
            [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
            [[-1., 0., 0.], [0., -1., 0.], [0., 0., 1.]],
            [[-1., 0., 0.], [0., 1., 0.], [0., 0., -1.]],
            [[1., 0., 0.], [0., -1., 0.], [0., 0., -1.]],
            [[0., 1., 0.], [1., 0., 0.], [0., 0., -1.]],
            [[0., -1., 0.], [-1., 0., 0.], [0., 0., -1.]],
            [[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]],
            [[0., 1., 0.], [-1., 0., 0.], [0., 0., 1.]],
            [[0., 0., 1.], [0., -1., 0.], [1., 0., 0.]],
            [[0., 0., -1.], [0., -1., 0.], [-1., 0., 0.]],
            [[0., 0., -1.], [0., 1., 0.], [1., 0., 0.]],
            [[0., 0., 1.], [0., 1., 0.], [-1., 0., 0.]],
            [[-1., 0., 0.], [0., 0., 1.], [0., 1., 0.]],
            [[-1., 0., 0.], [0., 0., -1.], [0., -1., 0.]],
            [[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]],
            [[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]],
            [[0., 0., 1.], [1., 0., 0.], [0., 1., 0.]],
            [[0., 0., -1.], [-1., 0., 0.], [0., 1., 0.]],
            [[0., 0., -1.], [1., 0., 0.], [0., -1., 0.]],
            [[0., 0., 1.], [-1., 0., 0.], [0., -1., 0.]],
            [[0., 1., 0.], [0., 0., 1.], [1., 0., 0.]],
            [[0., -1., 0.], [0., 0., -1.], [1., 0., 0.]],
            [[0., -1., 0.], [0., 0., 1.], [-1., 0., 0.]],
            [[0., 1., 0.], [0., 0., -1.], [-1., 0., 0.]],
            [[cos3, sin3, 0.], [msin3, cos3, 0.], [0., 0., 1.]],
            [[cos3, msin3, 0.], [sin3, cos3, 0.], [0., 0., 1.]],
            [[mcos3, sin3, 0.], [msin3, mcos3, 0.], [0., 0., 1.]],
            [[mcos3, msin3, 0.], [sin3, mcos3, 0.], [0., 0., 1.]],
            [[cos3, msin3, 0.], [msin3, mcos3, 0.], [0., 0., -1.]],
            [[cos3, sin3, 0.], [sin3, mcos3, 0.], [0., 0., -1.]],
            [[mcos3, msin3, 0.], [msin3, cos3, 0.], [0., 0., -1.]],
            [[mcos3, sin3, 0.], [sin3, cos3, 0.], [0., 0., -1.]],
        ]

        # Names for the 32 matrices, with and without inversion
        matrices_name = [
            'identity                                     ',
            '180 deg rotation - cart. axis [0,0,1]        ',
            '180 deg rotation - cart. axis [0,1,0]        ',
            '180 deg rotation - cart. axis [1,0,0]        ',
            '180 deg rotation - cart. axis [1,1,0]        ',
            '180 deg rotation - cart. axis [1,-1,0]       ',
            ' 90 deg rotation - cart. axis [0,0,-1]       ',
            ' 90 deg rotation - cart. axis [0,0,1]        ',
            '180 deg rotation - cart. axis [1,0,1]        ',
            '180 deg rotation - cart. axis [-1,0,1]       ',
            ' 90 deg rotation - cart. axis [0,1,0]        ',
            ' 90 deg rotation - cart. axis [0,-1,0]       ',
            '180 deg rotation - cart. axis [0,1,1]        ',
            '180 deg rotation - cart. axis [0,1,-1]       ',
            ' 90 deg rotation - cart. axis [-1,0,0]       ',
            ' 90 deg rotation - cart. axis [1,0,0]        ',
            '120 deg rotation - cart. axis [-1,-1,-1]     ',
            '120 deg rotation - cart. axis [-1,1,1]       ',
            '120 deg rotation - cart. axis [1,1,-1]       ',
            '120 deg rotation - cart. axis [1,-1,1]       ',
            '120 deg rotation - cart. axis [1,1,1]        ',
            '120 deg rotation - cart. axis [-1,1,-1]      ',
            '120 deg rotation - cart. axis [1,-1,-1]      ',
            '120 deg rotation - cart. axis [-1,-1,1]      ',
            ' 60 deg rotation - cryst. axis [0,0,1]       ',
            ' 60 deg rotation - cryst. axis [0,0,-1]      ',
            '120 deg rotation - cryst. axis [0,0,1]       ',
            '120 deg rotation - cryst. axis [0,0,-1]      ',
            '180 deg rotation - cryst. axis [1,-1,0]      ',
            '180 deg rotation - cryst. axis [2,1,0]       ',
            '180 deg rotation - cryst. axis [0,1,0]       ',
            '180 deg rotation - cryst. axis [1,1,0]       ',
            'inversion                                    ',
            'inv. 180 deg rotation - cart. axis [0,0,1]   ',
            'inv. 180 deg rotation - cart. axis [0,1,0]   ',
            'inv. 180 deg rotation - cart. axis [1,0,0]   ',
            'inv. 180 deg rotation - cart. axis [1,1,0]   ',
            'inv. 180 deg rotation - cart. axis [1,-1,0]  ',
            'inv.  90 deg rotation - cart. axis [0,0,-1]  ',
            'inv.  90 deg rotation - cart. axis [0,0,1]   ',
            'inv. 180 deg rotation - cart. axis [1,0,1]   ',
            'inv. 180 deg rotation - cart. axis [-1,0,1]  ',
            'inv.  90 deg rotation - cart. axis [0,1,0]   ',
            'inv.  90 deg rotation - cart. axis [0,-1,0]  ',
            'inv. 180 deg rotation - cart. axis [0,1,1]   ',
            'inv. 180 deg rotation - cart. axis [0,1,-1]  ',
            'inv.  90 deg rotation - cart. axis [-1,0,0]  ',
            'inv.  90 deg rotation - cart. axis [1,0,0]   ',
            'inv. 120 deg rotation - cart. axis [-1,-1,-1]',
            'inv. 120 deg rotation - cart. axis [-1,1,1]  ',
            'inv. 120 deg rotation - cart. axis [1,1,-1]  ',
            'inv. 120 deg rotation - cart. axis [1,-1,1]  ',
            'inv. 120 deg rotation - cart. axis [1,1,1]   ',
            'inv. 120 deg rotation - cart. axis [-1,1,-1] ',
            'inv. 120 deg rotation - cart. axis [1,-1,-1] ',
            'inv. 120 deg rotation - cart. axis [-1,-1,1] ',
            'inv.  60 deg rotation - cryst. axis [0,0,1]  ',
            'inv.  60 deg rotation - cryst. axis [0,0,-1] ',
            'inv. 120 deg rotation - cryst. axis [0,0,1]  ',
            'inv. 120 deg rotation - cryst. axis [0,0,-1] ',
            'inv. 180 deg rotation - cryst. axis [1,-1,0] ',
            'inv. 180 deg rotation - cryst. axis [2,1,0]  ',
            'inv. 180 deg rotation - cryst. axis [0,1,0]  ',
            'inv. 180 deg rotation - cryst. axis [1,1,0]  '
        ]

        rotations = []

        for key, value in zip(matrices_name[:len(transposed_matrices_cartesian)], transposed_matrices_cartesian):
            rotations.append({'name': key, 'matrix': numpy.transpose(value), 'inversion': False})

        for key, value in zip(matrices_name[len(transposed_matrices_cartesian):], transposed_matrices_cartesian):
            rotations.append({'name': key, 'matrix': numpy.transpose(value), 'inversion': True})

        return rotations

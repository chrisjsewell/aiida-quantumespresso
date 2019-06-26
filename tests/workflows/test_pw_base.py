import io
import pytest
from aiida import orm
from aiida.engine import run_get_node
from aiida.plugins import WorkflowFactory
from aiida.cmdline.utils.common import get_workchain_report, get_calcjob_report

from aiida_quantumespresso.utils.resources import get_default_options


def test_pw_base_default(fixture_database, fixture_computer_localhost, fixture_sandbox_folder, generate_calc_job,
                         generate_code_localhost, generate_structure, generate_kpoints_mesh, generate_upf_data, data_regression):
    entry_point_name = 'quantumespresso.pw'
    a = 5.43
    structure = orm.StructureData(
        cell=[[a / 2., a / 2., 0], [a / 2., 0, a / 2.], [0, a / 2., a / 2.]])
    structure.append_atom(position=(0., 0., 0.), symbols='Si', name="Si1")
    structure.append_atom(position=(a / 4., a / 4., a / 4.),
                          symbols='Si', name="Si2")
    structure.store()

    parameters = {
        'CONTROL': {
            'calculation': 'relax'
        },
        'SYSTEM': {
            'ecutrho': 240.0,
            'ecutwfc': 30.0
        }
    }
    kpoints = orm.KpointsData()
    kpoints.set_cell_from_structure(structure)
    kpoints.set_kpoints_mesh_from_density(0.15)

    computer = fixture_computer_localhost
    computer.configure()

    from aiida.orm import UpfData
    upf_file_path = "/Users/cjs14/GitHub/aiida-cjs-working/common_nodes/qe_pseudos/pbe-rrkj/Si.pbe-rrkj.UPF"
    with io.open(upf_file_path, 'r') as handle:
        upf = UpfData(file=handle.name)

    inputs = {
        "pw": {
            'code': generate_code_localhost(entry_point_name, computer),
            'structure': structure,
            'parameters': orm.Dict(dict=parameters),
            'settings': orm.Dict(),
            'pseudos': {'Si1': upf, 'Si2': upf},
            'metadata': {'options': get_default_options()}
        },
        'kpoints': kpoints,
    }

    wkchain_cls = WorkflowFactory("quantumespresso.pw.base")

    results, wkchain = run_get_node(wkchain_cls, **inputs)

    print(get_workchain_report(wkchain, 'REPORT'))

    if not wkchain.is_finished_ok:
        print(get_workchain_report(wkchain, 'REPORT'))
        for calcjob in wkchain.called:
            print(calcjob)
            print(get_calcjob_report(calcjob))
        raise AssertionError(wkchain.exit_message)

    assert 'output_parameters' in results
    assert 'output_structure' in results

    data_regression.check({
        'output_parameters': results['output_parameters'].get_dict(),
        'output_structure': results['output_structure'].attributes
    })


def test_pw_relax_default(fixture_database, fixture_computer_localhost, fixture_sandbox_folder, generate_calc_job,
                          generate_code_localhost, generate_structure, generate_kpoints_mesh, generate_upf_data, data_regression):
    entry_point_name = 'quantumespresso.pw'
    a = 5.43
    structure = orm.StructureData(
        cell=[[a / 2., a / 2., 0], [a / 2., 0, a / 2.], [0, a / 2., a / 2.]])
    structure.append_atom(position=(0., 0., 0.), symbols='Si', name="Si1")
    structure.append_atom(position=(a / 4., a / 4., a / 4.),
                          symbols='Si', name="Si2")
    structure.store()

    parameters = {
        'CONTROL': {
            'calculation': 'relax'
        },
        'SYSTEM': {
            'ecutrho': 240.0,
            'ecutwfc': 30.0
        }
    }
    kpoints = orm.KpointsData()
    kpoints.set_cell_from_structure(structure)
    kpoints.set_kpoints_mesh_from_density(0.15)

    computer = fixture_computer_localhost
    computer.configure()

    from aiida.orm import UpfData
    upf_file_path = "/Users/cjs14/GitHub/aiida-cjs-working/common_nodes/qe_pseudos/pbe-rrkj/Si.pbe-rrkj.UPF"
    with io.open(upf_file_path, 'r') as handle:
        upf = UpfData(file=handle.name)

    inputs = {
        'structure': structure,
        'relaxation_scheme': orm.Str('relax'),
        "base": {
            "pw": {
                'code': generate_code_localhost(entry_point_name, computer),
                'parameters': orm.Dict(dict=parameters),
                'settings': orm.Dict(),
                'pseudos': {'Si1': upf, 'Si2': upf},
                'metadata': {'options': get_default_options()}
            },
            'kpoints': kpoints}
    }

    wkchain_cls = WorkflowFactory("quantumespresso.pw.relax")

    results, wkchain = run_get_node(wkchain_cls, **inputs)

    print(get_workchain_report(wkchain, 'REPORT'))

    if not wkchain.is_finished_ok:
        print(get_workchain_report(wkchain, 'REPORT'))
        for calcjob in wkchain.called:
            print(calcjob)
            print(get_calcjob_report(calcjob))
        raise AssertionError(wkchain.exit_message)

    assert 'output_parameters' in results
    assert 'output_structure' in results

    data_regression.check({
        'output_parameters': results['output_parameters'].get_dict(),
        'output_structure': results['output_structure'].attributes
    })

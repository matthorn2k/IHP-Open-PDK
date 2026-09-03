# Copyright 2024 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Original file changed by IHP PDK Authors 2025
# Adopting the Skywater PDK import_netlist.py file to IHP SG13G2 technology


import os
import re
import sys
import pya

from .ihp130_pcell_templates import templates

# Debugging:
import pprint


SI_MULTIPLIERS = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "µ": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


def parse_si_value(value):
    if not isinstance(value, str):
        return value

    cleaned = value.strip()
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([a-zA-Zµ]+)?", cleaned)
    if not match:
        return value

    number = float(match.group(1))
    suffix = match.group(2)
    if not suffix:
        return number

    suffix = suffix.lower()
    if suffix in SI_MULTIPLIERS:
        return number * SI_MULTIPLIERS[suffix]

    if suffix.endswith("m") and len(suffix) == 2:
        prefix = suffix[0]
        if prefix in SI_MULTIPLIERS:
            return number * SI_MULTIPLIERS[prefix]

    return value


PARAM_EXPR_RE = re.compile(r'{([A-Za-z_][A-Za-z0-9_]*)}')


def parse_subckt_default_parameters(line):
    params = {}
    tokens = line.split()
    for token in tokens[2:]:
        if '=' in token:
            name, value = token.split('=', 1)
            params[name] = value
    return params


def get_subckt_name_from_instance(line):
    tokens = line.split()
    if len(tokens) < 2:
        return None
    for token in reversed(tokens[1:]):
        if '=' not in token:
            return token
    return None


def substitute_parameters(line, parameters):
    def replace(matchobj):
        name = matchobj.group(1)
        if name in parameters:
            return str(parameters[name])
        print(f'Error: Unknown parameter "{name}"')
        return matchobj.group(0)
    return PARAM_EXPR_RE.sub(replace, line)


def parse_instance_parameters(line):
    params = {}
    tokens = line.split()
    for token in tokens[1:]:
        if '=' in token:
            name, value = token.split('=', 1)
            params[name] = value
    return params


def normalize_params(params):
    normalized = {}
    for key, value in params.items():
        normalized[key] = parse_si_value(value)
    return normalized


def create_pcell_instance(pcell_name='CIRCLE', lib_name='Basic', params={}, pos=pya.Trans.R0):
    """
    Create a new instance of a PCell
    and return its width and height
    """

    print(f"Creating PCell '{pcell_name}' with parameters:")
    
    for key, value in params.items():
        print(f' - {key}: {value}')

    # # Debugging
    # for name in pya.Library.library_names():
    #     libb = pya.Library.library_by_name(name)
    #     print(repr(name), "=>", libb)

    # print("lib_name:", repr(lib_name))
    # print("available:", [repr(x) for x in pya.Library.library_names()])

    # print(type(lib_name))
    # print(repr(lib_name))

    # for name in pya.Library.library_names():
    #     print(repr(name), name == lib_name)

    # print("Requested:", repr(lib_name))

    # Get PCell Library
    lib = pya.Library.library_by_name(lib_name)

    # # Debugging
    # print("Result:", lib)
    # print("Result type:", type(lib))


    if not lib:
        print(f'Error: Library not found {lib_name}')
        return (0, 0)

    # The PCell Declaration. This one will create PCell variants.
    pcell_decl = lib.layout().pcell_declaration(pcell_name)

    if not pcell_decl:
        print(f'Error: Pcell not found {pcell_name}')
        return (0, 0)

    # Get the active layout
    cellview = pya.CellView().active()
    layout = cellview.layout()
    if layout == None:
        print(f'Error: Couldn\'t get active layout.')
        return

    # Get the top cell. Assuming only one top cell exists
    top_cell = layout.top_cell()

    # Add a PCell variant
    pcell_var = layout.add_pcell_variant(lib, pcell_decl.id(), params)
    
    bbox = layout.cell(pcell_var).bbox()
    
    # Add an offset to the position to account for the origin
    offset = pya.Trans(pos, x=-bbox.left, y=-bbox.bottom)

    width = bbox.width()
    height = bbox.height()
    
    # Insert instance
    top_cell.insert(pya.CellInstArray(pcell_var, offset))
    
    return (width, height)

current_x = 0
spacing = 100

def create_subckt_instance(name, subckt_definitions, global_parameters, instance_parameters=None):
    global current_x
    global spacing

    # Debugging
    print("The subckt name:")
    print(name)

    if name not in subckt_definitions:
        print(f'Error: Unknown subckt {name}')
        return

    effective_params = {}
    effective_params.update(global_parameters)
    effective_params.update(subckt_definitions[name].get('default_params', {}))
    if instance_parameters:
        effective_params.update(instance_parameters)

    for line in subckt_definitions[name].get('lines', []):
        stripped = line.strip()
        if not stripped or stripped.startswith('*'):
            continue
        if stripped.lower().startswith('.control') or stripped.lower().startswith('.endc'):
            continue
        if stripped.lower().startswith('.subckt') or stripped.lower().startswith('.ends'):
            continue

        substituted = substitute_parameters(stripped, effective_params)

        any_match = False
        for template in templates:
            match = template['regex'].match(substituted)
            if match:
                any_match = True
                params = normalize_params(template['default_params'])
                for param in template['params']:
                    raw_value = match.group(param['name'])

                    # Keep the template default if an optional parameter
                    # was not present in the netlist.
                    if raw_value is None:
                        continue

                    if param['type'] == 'string':
                        params[param['name']] = parse_si_value(raw_value)
                    elif param['type'] == 'int':
                        params[param['name']] = int(parse_si_value(raw_value))
                    elif param['type'] == 'float':
                        params[param['name']] = float(parse_si_value(raw_value))

                m = 1
                if 'm' in params:
                    m = params.pop('m')

                if 'nf' in params and params['nf'] > 1 and 'w' in params:
                    params['w'] /= params['nf']

                print("PCELL LIBRARY:", repr(template['pcell_library']))
                for _ in range(m):
                    (width, height) = create_pcell_instance(
                        template['pcell_name'],
                        template['pcell_library'],
                        params.copy(),
                        pya.Trans(current_x, 0)
                    )
                    current_x += width + spacing

        if any_match:
            continue

        if stripped.startswith('x') or stripped.startswith('X'):
            subckt_name = get_subckt_name_from_instance(substituted)
            if not subckt_name:
                print(f'Error: Could not parse subckt name in line "{stripped}"')
                continue

            instance_params = parse_instance_parameters(substituted)
            child_parameters = {}
            child_parameters.update(effective_params)
            child_parameters.update(instance_params)

            create_subckt_instance(subckt_name, subckt_definitions, global_parameters, child_parameters)

def ihp130_import_netlist():

    # Get the schematic netlist
    netlist_path = pya.FileDialog.ask_open_file_name("Choose the schematic netlist", '.', "SPICE (*.spice *.cir)")

    print()
    print(f'Info: The netlist importer is still experimental.')
    
    # Check whether file exists
    if not netlist_path or not os.path.isfile(netlist_path):
        print(f'Error: {netlist_path} is not a file!')
        sys.exit(0)

    print(f'Reading Spice netlist: {netlist_path}')

    # Parse the spice netlist
    with open(netlist_path, 'r') as netlist_file:
        netlist_content = netlist_file.read()

    # Continue lines starting with '+'
    netlist_content = netlist_content.replace('\n+', '')
    
    # Split lines
    netlist_lines = netlist_content.split('\n')

    # # Debugging
    # print("The netlist_lines:")
    # print(netlist_lines) # A python list of strings that hold the lines of the netlist file

    # Subckt data
    subckt_definitions = {
        'root': {
            'lines': [],
            'default_params': {},
            'references': 0,
        }
    }
    active_subckt = 'root'
    in_control = False

    # Parameter data
    global_parameters = {}

    # Handle ".include" statements
    found = True
    while found:
        found = False
        for i, line in enumerate(netlist_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith('*'):
                continue

            if stripped.lower().startswith('.include'):
                found = True
                include_path = stripped.split(maxsplit=1)[1].strip().strip('"').strip("'")

                if not os.path.isabs(include_path):
                    include_path = os.path.join(os.path.dirname(netlist_path), include_path)

                with open(include_path, 'r') as include_file:
                    include_content = include_file.read()

                include_content = include_content.replace('\n+', '')
                include_lines = include_content.split('\n')
                netlist_lines[i:i+1] = include_lines
                break

    # Collect global parameters and subckt bodies
    for line in netlist_lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.lower().startswith('.control'):
            in_control = True
            continue
        if stripped.lower().startswith('.endc'):
            in_control = False
            continue
        if in_control:
            continue
        if stripped.startswith('*'):
            continue
        if stripped.lower().startswith('.include'):
            continue

        if stripped.lower().startswith('.subckt'):
            tokens = stripped.split()
            active_subckt = tokens[1]
            subckt_definitions[active_subckt] = {
                'lines': [],
                'default_params': parse_subckt_default_parameters(stripped),
                'references': 0,
            }
            continue

        if stripped.lower().startswith('.ends'):
            active_subckt = 'root'
            continue

        if stripped.lower().startswith('.param'):
            parts = stripped.split(maxsplit=1)
            if len(parts) < 2:
                continue
            parameter = parts[1]
            if '=' in parameter:
                name, value = parameter.split('=', 1)
                global_parameters[name] = value
            continue

        subckt_definitions[active_subckt]['lines'].append(stripped)

    # Count subckt references
    for definition in subckt_definitions.values():
        for line in definition['lines']:
            stripped = line.strip()
            if stripped.lower().startswith('x'):
                subckt_name = get_subckt_name_from_instance(stripped)
                if subckt_name and subckt_name in subckt_definitions:
                    subckt_definitions[subckt_name]['references'] += 1

    # Debugging:
    # Creating a PrettyPrinter object with specific indentation
    pp = pprint.PrettyPrinter(indent=2, width=50)
    print("subckt_definitions:")
    pp.pprint(subckt_definitions)

    # Instantiate all root-level subckts and root-level lines
    for name in list(subckt_definitions.keys()):
        if name == 'root' or subckt_definitions[name]['references'] == 0:
            create_subckt_instance(name, subckt_definitions, global_parameters)

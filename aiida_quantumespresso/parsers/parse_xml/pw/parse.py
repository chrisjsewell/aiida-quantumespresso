# -*- coding: utf-8 -*-
import xmlschema
from defusedxml import ElementTree
from .versions import get_schema_filepath, get_default_schema_filepath


def parse_pw_xml_post_6_2(xml_file):
    """
    """
    try:
        xml = ElementTree.parse(xml_file)
    except IOError:
        raise ValueError('could not open and or parse the XML file {}'.format(xml_file))

    schema_filepath = get_schema_filepath(xml)

    try:
        xsd = xmlschema.XMLSchema(schema_filepath)
    except URLError:

        # If loading the XSD file specified in the XML file fails, we try the default
        schema_filepath = get_default_schema_filepath()

        try:
            xsd = xmlschema.XMLSchema(schema_filepath)
        except URLError:
            raise ValueError('could not open and or parse the XSD file {}'.format(schema_filepath))

    result = xsd.to_dict(xml, validation='lax')

    if isinstance(result, tuple):
        xml_dictionary, errors = result
    else:
        xml_dictionary = result
        errors = []

    return xml_dictionary, {}, {}

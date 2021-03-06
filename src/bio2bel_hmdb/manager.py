# -*- coding: utf-8 -*-

"""The Manager is a key component of HMDB. This class is used to create, populate and query the local HMDB version."""

import logging
from bio2bel import AbstractManager
from tqdm import tqdm

from pybel.resources.definitions import get_bel_resource
from .constants import MODULE_NAME, ONTOLOGY_NAMESPACES, ONTOLOGIES, DOID, MESHD, HP
from .models import (
    Base, Biofluid, Biofunction, CellularLocation, Disease, Metabolite, MetaboliteBiofluid, MetaboliteBiofunction,
    MetaboliteCellularLocation, MetaboliteDiseaseReference, MetabolitePathway, MetaboliteProtein, MetaboliteReference,
    MetaboliteSynonym, MetaboliteTissue, Pathway, Protein, Reference, SecondaryAccession, Tissue,
)
from .parser import get_data

log = logging.getLogger(__name__)


class Manager(AbstractManager):
    """Managers handle the database construction, population and querying."""

    module_name = MODULE_NAME
    flask_admin_models = [Metabolite, Disease, Protein, Pathway, Biofluid]

    @property
    def _base(self):
        """Returns the declarative base for HMDB"""
        return Base

    def is_populated(self):
        """Check if the database is already populated.

        :rtype: bool
        """
        return 0 < self.count_metabolites()

    @staticmethod
    def _get_tag(element_tag):
        """Delete the XML namespace prefix when calling element.tag

        :param element_tag: tag attribute of an XML element
        :rtype: str
        """
        return element_tag.split("}")[1]

    def _populate_with_1_layer_elements(self, element, metabolite_instance, instance_dict, table, relation_table,
                                        column_name):
        """Parse and populate database with metabolite elements, which themselfes have one more layer.

        :param element: the current parent XML element. E.g. "pathways" where the children would have the tag "pathway".
        :param models.Metabolite metabolite_instance: metabolite object which is associated with the instances (e.g. is
                                                      involved in that "pathway")
        :param dict instance_dict: dictionary which tracks if the found instance is already present in the table and can
                                   then refer to it
        :param class table: sqlalchemy class to which the instances belong. E.g. "Pathways"
        :param class relation_table: sqlalchemy class which stores the many to many relation between the instances and
                                     the metabolites
        :param str column_name: Name of the column in the relation tables which does not represent the metabolite.
                                e.g. reference, pathway etc
        :rtype: dict
        """
        for instance_element in element:
            instance_dict_key = instance_element.text

            if instance_dict_key not in instance_dict:  # check if instance is already in table
                new_instance_dict = {column_name: instance_dict_key}
                instance_dict[instance_dict_key] = table(**new_instance_dict)
                self.session.add(instance_dict[instance_dict_key])

            # create metabolite-instance relation object
            new_meta_rel_dict = {"metabolite": metabolite_instance, column_name: instance_dict[instance_dict_key]}
            self.session.add(relation_table(**new_meta_rel_dict))
        return instance_dict

    def _populate_with_2_layer_elements(self, element, metabolite_instance, instance_dict, table, relation_table,
                                        column, instance_dict_key=None, metabolite_column='metabolite'):
        """Parse and populate database with metabolite elements, which themselves have two more layers.

        :param element: the current parent XML element. E.g. "pathways" where the children would have the tag "pathway".
        :param models.Metabolite metabolite_instance: metabolite object which is associated with the instances (e.g. is
        involved in that "pathway")
        :param dict instance_dict: dictionary which tracks if the found instance is already present in the table and can
        then refer to it
        :param type table: sqlalchemy class to which the instances belong. E.g. "Pathways"
        :param type relation_table: sqlalchemy class which stores the many to many relation between the instances and
        the metabolites
        :param str column: column name of the relation table which is not the metabolite
        :param str instance_dict_key: String which is used as the key for the instance_dict. (to ensure uniqueness in
        the instance_dict)
        :param str metabolite_column: column of the relation table which represents the foreignkey to the main table.
        In our database model the Metabolite table.
        :rtype: dict
        """
        if instance_dict_key is None and len(element) > 0:
            instance_dict_key = self._get_tag(element[0][0].tag)

        for instance_element in element:
            # build pathway object dict to create pathway object
            instance_object_dict = {}

            # create pathway instance
            for instance_sub_element in instance_element:
                cutted_pathway_tag = self._get_tag(instance_sub_element.tag)
                instance_object_dict[cutted_pathway_tag] = instance_sub_element.text

            # add MetabolitePathway relation and continue with next pathway if pathway already present in Pathways
            if instance_object_dict[instance_dict_key] in instance_dict:
                new_meta_rel_dict = {
                    metabolite_column: metabolite_instance,
                    column: instance_dict[instance_object_dict[instance_dict_key]]
                }
                new_meta_rel = relation_table(**new_meta_rel_dict)
                self.session.add(new_meta_rel)
                continue

            instance_dict[instance_object_dict[instance_dict_key]] = table(**instance_object_dict)
            self.session.add(instance_dict[instance_object_dict[instance_dict_key]])

            new_meta_rel_dict = {
                metabolite_column: metabolite_instance,
                column: instance_dict[instance_object_dict[instance_dict_key]]
            }
            new_meta_rel = relation_table(**new_meta_rel_dict)
            self.session.add(new_meta_rel)

        return instance_dict

    def _populate_diseases(self, element, references_dict, diseases_dict, metabolite_instance, disease_ontologies=None,
                           map_dis=True):
        """Populates the database with disease and related reference information.

        :param element: Element object from the xml ElementTree
        :param dict references_dict: Dictionary to keep track of which references are already in the database
        :param dict diseases_dict: Dictionary to keep track of which diseases are already in the database
        :param models.Metabolite metabolite_instance: Metabolite object to which the diseases and references are related
        :param boolean map_dis: If True the HMDB disease names will be mapped to different ontologies.
        :rtype: dict, dict
        """
        for disease_element in element:
            disease_instance = Disease()

            for disease_sub_element in disease_element:

                dtag = self._get_tag(disease_sub_element.tag)

                if dtag != "references":
                    setattr(disease_instance, dtag, disease_sub_element.text)

                else:
                    if disease_instance.name not in diseases_dict:  # add disease instance if not already in table
                        # map to different disease ontologies if map is True
                        if map_dis:
                            disease_lower = disease_instance.name.lower()  # for case insensitivity
                            for ontology in disease_ontologies:
                                if disease_lower not in disease_ontologies[ontology]:
                                    continue

                                v = disease_ontologies[ontology][disease_lower]

                                if ontology == DOID:
                                    setattr(disease_instance, 'dion', v)
                                elif ontology == HP:
                                    setattr(disease_instance, 'hpo', v)
                                elif ontology == MESHD:
                                    setattr(disease_instance, 'mesh_diseases', v)

                        diseases_dict[disease_instance.name] = disease_instance
                        self.session.add(disease_instance)

                    for reference_element in disease_sub_element:
                        new_reference_object_dict = {}  # dict to check if reference is already presend in table

                        for reference_sub_element in reference_element:  # construct new reference object
                            reference_tag = self._get_tag(reference_sub_element.tag)
                            new_reference_object_dict[reference_tag] = reference_sub_element.text

                        # add if not already in reference table
                        if new_reference_object_dict['reference_text'] not in references_dict:
                            references_dict[new_reference_object_dict['reference_text']] = Reference(
                                **new_reference_object_dict)
                            self.session.add(references_dict[new_reference_object_dict['reference_text']])

                        rel_meta_dis_ref = MetaboliteDiseaseReference(
                            metabolite=metabolite_instance,
                            disease=diseases_dict[disease_instance.name],
                            reference=references_dict[new_reference_object_dict['reference_text']]
                        )
                        self.session.add(rel_meta_dis_ref)
        return references_dict, diseases_dict

    @staticmethod
    def _disease_ontology_dict(ontology):
        """Creates dictionaries from the disease ontologies used for mapping HMDB disease names to those ontologies.

        :rtype: dict
        """
        doid_path = ONTOLOGY_NAMESPACES[ontology]
        doid_ns = get_bel_resource(doid_path)
        return {value.lower(): value for value in doid_ns['Values']}

    def populate(self, source=None, map_dis=True):
        """Populates the database with the HMDB data

        :param Optional[str] source: Path to an .xml file. If None the whole HMDB will be downloaded and used for
         population.
        :param bool map_dis: Should diseases be mapped?
        """

        # construct sets for disease ontologies for mapping hmdb diseases
        if map_dis:
            disease_ontologies = dict()

            for ontology in ONTOLOGIES:
                disease_ontologies[ontology] = self._disease_ontology_dict(ontology)
        else:
            disease_ontologies = None

        # construct xml tree
        tree = get_data(source)
        root = tree.getroot()


        # dicts to check unique constraints for specific tables
        biofluids_dict = {}
        tissues_dict = {}
        pathways_dict = {}
        proteins_dict = {}
        references_dict = {}
        diseases_dict = {}
        biofunctions_dict = {}
        cellular_locations_dict = {}

        # iterate through xml tree
        for metabolite in tqdm(root, desc='HMDB Metabolite'):
            # create metabolite dict used to feed in main metabolite table
            metabolite_instance = Metabolite()

            for element in metabolite:
                # delete namespace prefix
                tag = self._get_tag(element.tag)

                # handle wikipedia typo in xml tags
                if tag == "wikipidia":
                    tag = "wikipedia"
                elif tag == "wikipedia":
                    log.warning("HMDB fixed the 'wikipidia' tag to 'wikipedia'. Change code.")

                if tag == "secondary_accessions":
                    for secondary_accession_element in element:
                        new_secondary_accession = SecondaryAccession(
                            metabolite=metabolite_instance,
                            secondary_accession=secondary_accession_element.text
                        )
                        self.session.add(new_secondary_accession)

                elif tag == "synonyms":
                    for synonym_element in element:
                        new_synonym = MetaboliteSynonym(metabolite=metabolite_instance, synonym=synonym_element.text)
                        self.session.add(new_synonym)

                elif tag == "taxonomy":  # will be delayed to later versions since not important for BEL
                    continue

                elif tag == "ontology":
                    continue


                elif tag == "cellular_locations":
                    cellular_locations_dict = self._populate_with_1_layer_elements(element,
                                                                                   metabolite_instance,
                                                                                   cellular_locations_dict,
                                                                                   CellularLocation,
                                                                                   MetaboliteCellularLocation,
                                                                                   "cellular_location")

                elif tag == "experimental_properties":  # will be delayed to later versions since not important for BEL
                    continue

                elif tag == "predicted_properties":  # will be delayed to later versions since not important for BEL
                    continue

                elif tag == "spectra":  # will not be processed since the corresponding database is down
                    continue

                elif tag == "biospecimen_locations":
                    biofluids_dict = self._populate_with_1_layer_elements(element, metabolite_instance, biofluids_dict,
                                                                          Biofluid, MetaboliteBiofluid, 'biofluid')

                elif tag == "tissue_locations":
                    tissues_dict = self._populate_with_1_layer_elements(element, metabolite_instance, tissues_dict,
                                                                        Tissue, MetaboliteTissue, 'tissue')

                elif tag == "pathways":
                    pathways_dict = self._populate_with_2_layer_elements(element, metabolite_instance, pathways_dict,
                                                                         Pathway, MetabolitePathway, 'pathway')

                elif tag == "normal_concentrations":  # will be delayed to later versions since not important for BEL
                    continue
                elif tag == "abnormal_concentrations":  # will be delayed to later versions since not important for BEL
                    continue

                elif tag == "diseases":
                    references_dict, diseases_dict = self._populate_diseases(element, references_dict,
                                                                             diseases_dict, metabolite_instance,
                                                                             disease_ontologies, map_dis=map_dis)

                elif tag == "general_references":
                    references_dict = self._populate_with_2_layer_elements(element, metabolite_instance,
                                                                           references_dict,
                                                                           Reference, MetaboliteReference,
                                                                           'reference',
                                                                           "reference_text")

                elif tag == "protein_associations":
                    proteins_dict = self._populate_with_2_layer_elements(element, metabolite_instance, proteins_dict,
                                                                         Protein, MetaboliteProtein, 'protein')

                else:  # feed in main metabolite table

                    setattr(metabolite_instance, tag, element.text)


            self.session.add(metabolite_instance)

        self.session.commit()

    def get_metabolite_by_accession(self, hmdb_metabolite_accession):
        """Query the constructed HMDB database and extract a metabolite object.

        :param str hmdb_metabolite_accession: HMDB metabolite identifier
        :rtype: Optional[models.Metabolite]

        Example:

        >>> import bio2bel_hmdb
        >>> manager = bio2bel_hmdb.Manager()
        >>> manager.get_metabolite_by_accession("HMDB00072")
        """
        return self.session.query(Metabolite).filter(Metabolite.accession == hmdb_metabolite_accession).one_or_none()

    def query_metabolite_associated_proteins(self, hmdb_metabolite_id):
        """Query the constructed HMDB database to get the metabolite associated protein relations for BEL enrichment

        :param str hmdb_metabolite_id: HMDB metabolite identifier
        :rtype: Optional[list[models.Protein]]
        """
        metabolite = self.get_metabolite_by_accession(hmdb_metabolite_id)

        if metabolite is None:
            return

        return metabolite.proteins

    def query_metabolite_associated_diseases(self, hmdb_metabolite_id):
        """Query the constructed HMDB database to get the metabolite associated disease relations for BEL enrichment

        :param str hmdb_metabolite_id: HMDB metabolite identifier
        :rtype: list
        """
        metabolite = self.get_metabolite_by_accession(hmdb_metabolite_id)
        return metabolite.diseases

    def query_disease_associated_metabolites(self, disease_name):
        """Query function that returns a list of metabolite-disease interactions, which are associated to a disease.

        :param disease_name: HMDB disease name
        :rtype: list
        """
        return self.session.query(Disease).filter(Disease.name == disease_name).one_or_none().metabolites

    def query_protein_associated_metabolites(self, uniprot_id):
        """Query function that returns a list of metabolite-disease interactions, which are associated to a disease.

        :param str uniprot_id: uniprot identifier of a protein for which the associated metabolite relations should be
                                outputted
        :rtype: list
        """
        return self.session.query(Protein).filter(Protein.uniprot_id == uniprot_id).one_or_none().metabolites

    def get_hmdb_accession(self):
        """Create a list of all HMDB metabolite identifiers present in the database.

        :rtype: list
        """
        accessions = self.session.query(Metabolite.accession).all()
        if not accessions:
            log.warning("Database not populated. Please populate database before calling this function")

        return [a[0] for a in accessions]  # if anybody knows a better way of querying for a flat list. Please change.

    def get_hmdb_diseases(self):
        """Create a list of all disease names present in the database.

        :rtype: list
        """
        accessions = self.session.query(Disease.name).all()
        if not accessions:
            log.warning("Database not populated. Please populate database before calling this function")

        return [a for a, in accessions]

    def _get_models(self, interaction_table):
        """Extracts all interactions from the many to many interaction table.

        :param type interaction_table: Relation table from the database model. (e.g. MetaboliteProteins)
        :rtype: query
        """
        return self.session.query(interaction_table).all()

    def get_metabolite_disease_interactions(self):
        """
        :rtype: list[MetaboliteDiseaseReference]
        """
        return self._get_models(MetaboliteDiseaseReference)

    def get_metabolite_protein_interactions(self):
        """
        :rtype: list[MetaboliteProtein]
        """
        return self._get_models(MetaboliteProtein)

    def count_diseases(self):
        """Counts the number of diseases in the database

        :rtype: int
        """
        return self.session.query(Disease).count()

    def count_cellular_locations(self):
        """Counts the number of cellular locations in the database

        :rtype: int
        """
        return self.session.query(CellularLocation).count()

    def count_references(self):
        """Counts the number of literature references in the database

        :rtype: int
        """
        return self.session.query(Reference).count()

    def get_reference_by_pubmed_id(self, pubmed_id):
        """Gets a reference by its PubMed identifier if it exists

        :param str pubmed_id: The PubMed identifier to search
        :rtype: Optional[Reference]
        """
        return self.session.query(Reference).filter(Reference.pubmed_id == pubmed_id).one_or_none()

    def count_proteins(self):
        """Counts the number of proteins in the database

        :rtype: int
        """
        return self.session.query(Protein).count()

    def count_biofunctions(self):
        """Counts the number of biofunctions in the database

        :rtype: int
        """
        return self.session.query(Biofunction).count()

    def count_metabolites(self):
        """Counts the number of metabolites in the database

        :rtype: int
        """
        return self._count_model(Metabolite)

    def count_pathways(self):
        """Counts the number of pathways in the database

        :rtype: int
        """
        return self._count_model(Pathway)

    def count_tissues(self):
        """Counts the number of tissues in the database

        :rtype: int
        """
        return self._count_model(Tissue)

    def summarize(self):
        """Summarizes the contents of the database in a dictionary

        :rtype: dict[str,int]
        """
        return dict(
            proteins=self.count_proteins(),
            diseases=self.count_diseases(),
            biofunctions=self.count_biofunctions(),
            references=self.count_references(),
            cellular_locations=self.count_cellular_locations(),
            metabolites=self.count_metabolites(),
            tissues=self.count_tissues()
        )


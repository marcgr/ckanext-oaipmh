# coding: utf-8
# vi:et:ts=8:

import logging
import json
from itertools import islice

import oaipmh.client
import oaipmh.error
from dateutil.parser import parse as dp

import importformats

from ckan.model import Session, Package
from ckan.logic import get_action, NotFound
from ckan import model

from ckanext.harvest.model import HarvestJob, HarvestObject
from ckanext.harvest.harvesters.base import HarvesterBase
import ckanext.kata.utils
import ckanext.kata.plugin

log = logging.getLogger(__name__)


class OAIPMHHarvester(HarvesterBase):
    '''
    OAI-PMH Harvester
    '''

    def _get_configuration(self, harvest_job):
        """ Parse configuration from given harvest object """
        configuration = {}
        if harvest_job.source.config:
            log.debug('Config: %s', harvest_job.source.config)
            try:
                configuration = json.loads(harvest_job.source.config)
            except ValueError as e:
                self._save_gather_error('Gather: Unable to decode config from: {c}, {e}'.
                                        format(e=e, c=harvest_job.source.config), harvest_job)
                raise
        return configuration

    def _create_or_update_package(self, package_dict, harvest_object, schema=None, s_schema=None):
        """ Add prevent-recreate functionality """
        configuration = self._get_configuration(harvest_object)

        recreate = configuration.get('recreate', configuration.get('type') != 'ida')
        if not recreate:
            try:
                package_id = package_dict['id']
                get_action('package_show')({'model': model, 'session': model.Session, 'user': 'harvest'}, {'id': package_id})
                log.debug("Not re-creating package: %s", package_id)
                return True
            except NotFound:
                pass

        return HarvesterBase._create_or_update_package(self, package_dict, harvest_object, schema=schema, s_schema=s_schema)


    def info(self):
        '''
        Harvesting implementations must provide this method, which will return a
        dictionary containing different descriptors of the harvester. The
        returned dictionary should contain:

        * name: machine-readable name. This will be the value stored in the
          database, and the one used by ckanext-harvest to call the appropiate
          harvester.
        * title: human-readable name. This will appear in the form's select box
          in the WUI.
        * description: a small description of what the harvester does. This will
          appear on the form as a guidance to the user.

        A complete example may be::

            {
                'name': 'csw',
                'title': 'CSW Server',
                'description': 'A server that implements OGC's Catalog Service
                                for the Web (CSW) standard'
            }

        :returns: A dictionary with the harvester descriptors
        '''

        log.debug("Entering info()")
        log.debug("Exiting info()")
        return {
            'name': 'oai-pmh',
            'title': 'OAI-PMH',
            'description': 'Harvests OAI-PMH providers'
        }

    def validate_config(self, config):
        '''

        [optional]

        Harvesters can provide this method to validate the configuration entered in the
        form. It should return a single string, which will be stored in the database.
        Exceptions raised will be shown in the form's error messages.

        :param harvest_object_id: Config string coming from the form
        :returns: A string with the validated configuration options
        '''

        # TODO: Tests

        def validate_param(d, p, t):
            '''
            Check if 'p' is specified and is of type 't'
            '''
            if p in d and not isinstance(d[p], t):
                raise TypeError("'{p}' needs to be a '{t}'".format(t=t, p=p))
            return p in d

        def validate_date_param(d, p, t):
            '''
            Validate a date parameter by trying to parse it
            '''
            if validate_param(d, p, t):
                dp(d[p]).replace(tzinfo=None)

        # Todo: Write better try/except cases
        if config:
            dj = json.loads(config)
            validate_param(dj, 'set', list)
            validate_param(dj, 'limit', int)
            validate_param(dj, 'type', basestring)
            validate_date_param(dj, 'until', basestring)
            validate_date_param(dj, 'from', basestring)
        else:
            config = '{}'
        return config

    # def get_original_url(self, harvest_object_id):
    #     '''
    #
    #     [optional]
    #
    #     This optional but very recommended method allows harvesters to return
    #     the URL to the original remote document, given a Harvest Object id.
    #     Note that getting the harvest object you have access to its guid as
    #     well as the object source, which has the URL.
    #     This URL will be used on error reports to help publishers link to the
    #     original document that has the errors. If this method is not provided
    #     or no URL is returned, only a link to the local copy of the remote
    #     document will be shown.
    #
    #     Examples:
    #         * For a CKAN record: http://{ckan-instance}/api/rest/{guid}
    #         * For a WAF record: http://{waf-root}/{file-name}
    #         * For a CSW record: http://{csw-server}/?Request=GetElementById&Id={guid}&...
    #
    #     :param harvest_object_id: HarvestObject id
    #     :returns: A string with the URL to the original document
    #     '''

    def gather_stage(self, harvest_job):
        '''
        The gather stage will receive a HarvestJob object and will be
        responsible for:
            - gathering all the necessary objects to fetch on a later.
              stage (e.g. for a CSW server, perform a GetRecords request)
            - creating the necessary HarvestObjects in the database, specifying
              the guid and a reference to its job. The HarvestObjects need a
              reference date with the last modified date for the resource, this
              may need to be set in a different stage depending on the type of
              source.
            - creating and storing any suitable HarvestGatherErrors that may
              occur.
            - returning a list with all the ids of the created HarvestObjects.

        :param harvest_job: HarvestJob object
        :returns: A list of HarvestObject ids
        :type harvest_job: HarvestJob
        '''

        def get_package_ids():
            '''
            '''
            # TODO! This should be cleaned up somewhat, ie. no globals, etc.

            def filter_map_args(list_tuple):
                for x, y in list_tuple:
                    if x in ['until', 'from']:
                        if x == 'from':
                            x = 'from_'
                        yield (x, dp(y).replace(tzinfo=None))

            args = dict(filter_map_args(config.items()))
            args['metadataPrefix'] = md_format
            if last_time and 'from_' not in args:
                args['from_'] = dp(last_time).replace(tzinfo=None)
            if set_ids:
                for set_id in set_ids:
                    for header in client.listIdentifiers(set=set_id, **args):
                        yield header.identifier()
            else:
                for header in client.listIdentifiers(**args):
                    yield header.identifier()
                    # package_ids = [header.identifier() for header in client.listRecords()]

        log.debug('Entering gather_stage()')

        log.debug('Harvest source: {s}'.format(s=harvest_job.source.url))

        config = self._get_configuration(harvest_job)
        harvest_type = config.get('type', 'default')
        # Create a OAI-PMH Client
        registry = importformats.create_metadata_registry(harvest_type)
        log.debug('Registry: {r}'.format(r=registry))
        client = oaipmh.client.Client(harvest_job.source.url, registry)
        log.debug('Client: {c}'.format(c=client))

        # Choose best md_format from md_formats,
        # but let's use 'oai_dc' for now
        try:
            md_formats = client.listMetadataFormats()
            md_format = 'oai_dc'
        except oaipmh.error.BadVerbError as e:
            log.warning('Provider does not support listMetadataFormats verb. Using oai_dc as fallback format.')
            md_format = 'oai_dc'
        log.debug('Metadata format: {mf}'.format(mf=md_format))

        set_ids = config.get('set', [])
        log.debug('Sets in config: %s' % set_ids)

        log.debug('listSets(): {s}'.format(s=list(client.listSets())))

        # Check if this source has been harvested before
        previous_job = Session.query(HarvestJob) \
            .filter(HarvestJob.source==harvest_job.source) \
            .filter(HarvestJob.gather_finished!=None) \
            .filter(HarvestJob.id!=harvest_job.id) \
            .order_by(HarvestJob.gather_finished.desc()) \
            .limit(1).first()

        last_time = None
        if previous_job and not previous_job.gather_errors and not len(previous_job.objects) == 0:
            # Request only the packages modified since last harvest job
            last_time = previous_job.gather_finished.isoformat()
            # url = base_search_url + '/revision?since_time=%s' % last_time
            if False:
                self._save_gather_error('Gather: Unable to get content for: {u}: {e}'.format(
                    u=harvest_job.source.url, e=e), harvest_job)

            if True:
                # for package_id in package_ids:
                #     if not package_id in package_ids:
                #         package_ids.append(package_id)
                pass
            else:
                log.info('No packages have been updated on the provider since the last harvest job')
                return None

        # Collect package ids
        package_ids = list(get_package_ids())
        log.debug('Identifiers: {i}'.format(i=package_ids))

        try:
            object_ids = []
            if len(package_ids):
                for package_id in islice(package_ids, config['limit']) if 'limit' in config else package_ids:
                    # Create a new HarvestObject for this identifier
                    obj = HarvestObject(guid=package_id, job=harvest_job)
                    obj.save()
                    object_ids.append(obj.id)
                log.debug('Object ids: {i}'.format(i=object_ids))
                return object_ids
            else:
                self._save_gather_error('No packages received for URL: {u}'.format(
                    u=harvest_job.source.url), harvest_job)
                return None
        except Exception as e:
            self._save_gather_error('Gather: {e}'.format(e=e), harvest_job)
            raise
        finally:
            log.debug("Exiting gather_stage()")

    def fetch_stage(self, harvest_object):
        '''
        The fetch stage will receive a HarvestObject object and will be
        responsible for:
            - getting the contents of the remote object (e.g. for a CSW server,
              perform a GetRecordById request).
            - saving the content in the provided HarvestObject.
            - creating and storing any suitable HarvestObjectErrors that may
              occur.
            - returning True if everything went as expected, False otherwise.

        :param harvest_object: HarvestObject object
        :returns: True if everything went right, False if errors were found
        '''

        log.debug("Entering fetch_stage()")
        log.debug("Exiting fetch_stage()")

        log.debug('Harvest object: %s' % harvest_object)
        log.debug('Harvest job: %s' % harvest_object.job)
        log.debug('Object id: %s' % harvest_object.guid)
        log.debug('Harvest job: %s' % dir(harvest_object))

        # Get metadata content from provider
        try:
            # Todo! This should not be duplicated here. Should be some class' attributes
            # Create a OAI-PMH Client
            config = self._get_configuration(harvest_object)
            harvest_type = config.get('type', 'default')
            registry = importformats.create_metadata_registry(harvest_type)
            client = oaipmh.client.Client(harvest_object.job.source.url, registry)
            # Choose best md_format from md_formats, but let's use 'oai_dc' for now
            md_format = 'oai_dc'

            # Get source URL
            header, metadata, about = client.getRecord(identifier=harvest_object.guid, metadataPrefix=md_format)
        except Exception as e:
            self._save_object_error('Unable to get metadata from provider: {u}: {e}'.format(
                u=harvest_object.source.url, e=e), harvest_object)
            return False

        # Get contents
        try:
            content = json.dumps(metadata.getMap())
        except Exception as e:
            self._save_object_error('Unable to get content for package: {u}: {e}'.format(
                u=harvest_object.source.url, e=e), harvest_object)
            return False

        # Save the fetched contents in the HarvestObject
        harvest_object.content = content
        harvest_object.save()

        return True

    def import_stage(self, harvest_object):
        '''
        The import stage will receive a HarvestObject object and will be
        responsible for:
            - performing any necessary action with the fetched object (e.g
              create a CKAN package).
              Note: if this stage creates or updates a package, a reference
              to the package should be added to the HarvestObject.
            - creating the HarvestObject - Package relation (if necessary)
            - creating and storing any suitable HarvestObjectErrors that may
              occur.
            - returning True if everything went as expected, False otherwise.

        :param harvest_object: HarvestObject object
        :returns: True if everything went right, False if errors were found
        '''

        log.debug("Entering import_stage()")

        if not harvest_object:
            log.error('No harvest object received')
            return False

        if harvest_object.content is None:
            self._save_object_error('Import: Empty content for object {id}'.format(
                id=harvest_object.id), harvest_object)
            return False

        log.debug('Content (packed): %s' % harvest_object.content)
        content = json.loads(harvest_object.content)
        log.debug('Content (unpacked): %s' % content)
        # import pprint; pprint.pprint(content)

        package_dict = content.pop('unified')
        package_dict['xpaths'] = content

        # If package exists use old PID, otherwise create new

        # TODO: Search with all dataset data pids against all data pids in database
        pkg = Session.query(Package).filter(Package.name == package_dict['name']).first()
        log.debug('Package: "{pkg}"'.format(pkg=pkg))
        package_dict['id'] = pkg.id if pkg else ckanext.kata.utils.generate_pid()

        try:
            package_dict['title'] = ''

            config = self._get_configuration(harvest_object)
            if config.get('type', 'default') != 'ida':
                schema = ckanext.kata.plugin.KataPlugin.update_package_schema_oai_dc() if pkg \
                    else ckanext.kata.plugin.KataPlugin.create_package_schema_oai_dc()
            else:
                package = model.Package.get(harvest_object.harvest_source_id)
                if package and package.owner_org:
                    package_dict['owner_org'] = package.owner_org
                    package_dict['private'] = "true"
                schema = ckanext.kata.plugin.KataPlugin.update_package_schema_oai_dc_ida() if pkg \
                    else ckanext.kata.plugin.KataPlugin.create_package_schema_oai_dc_ida()
            # schema['xpaths'] = [ignore_missing, ckanext.kata.converters.xpath_to_extras]
            result = self._create_or_update_package(package_dict,
                                                    harvest_object,
                                                    schema=schema,
                                                    # s_schema=ckanext.kata.plugin.KataPlugin.show_package_schema()
                                                    )
            log.debug("Exiting import_stage()")
        except Exception as e:
            self._save_object_error('Import: Could not create {id}. {e}'.format(
                id=harvest_object.id, e=e), harvest_object)
            return False

        return result

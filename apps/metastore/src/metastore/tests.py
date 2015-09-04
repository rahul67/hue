#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import urllib

from nose.plugins.skip import SkipTest
from nose.tools import assert_true, assert_equal, assert_false

from django.utils.encoding import smart_str
from django.contrib.auth.models import User, Group
from django.core.urlresolvers import reverse

from desktop.lib.django_test_util import make_logged_in_client, assert_equal_mod_whitespace
from desktop.lib.test_utils import add_permission, grant_access
from hadoop.pseudo_hdfs4 import is_live_cluster
from metastore import parser
from metastore.conf import HS2_GET_TABLES_MAX
from useradmin.models import HuePermission, GroupPermission, group_has_permission

from beeswax.conf import BROWSE_PARTITIONED_TABLE_LIMIT
from beeswax.views import collapse_whitespace
from beeswax.test_base import make_query, wait_for_query_to_finish, verify_history, get_query_server_config, fetch_query_result_data
from beeswax.models import QueryHistory
from beeswax.server import dbms
from beeswax.test_base import BeeswaxSampleProvider


LOG = logging.getLogger(__name__)


def _make_query(client, query, submission_type="Execute",
                udfs=None, settings=None, resources=[],
                wait=False, name=None, desc=None, local=True,
                is_parameterized=True, max=30.0, database='default', email_notify=False, **kwargs):
  """Wrapper around the real make_query"""
  res = make_query(client, query, submission_type,
                   udfs, settings, resources,
                   wait, name, desc, local, is_parameterized, max, database, email_notify, **kwargs)

  # Should be in the history if it's submitted.
  if submission_type == 'Execute':
    fragment = collapse_whitespace(smart_str(query[:20]))
    verify_history(client, fragment=fragment)

  return res


class TestMetastoreWithHadoop(BeeswaxSampleProvider):
  requires_hadoop = True

  def setUp(self):
    user = User.objects.get(username='test')
    self.db = dbms.get(user, get_query_server_config())

    add_permission("test", "test", "write", "metastore")

  def test_basic_flow(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    # Default database should exist
    response = self.client.get("/metastore/databases")
    assert_true(self.db_name in response.context["databases"])

    # Table should have been created
    response = self.client.get("/metastore/tables/")
    assert_equal(200, response.status_code)

    # Switch databases
    response = self.client.get("/metastore/tables/%s" % self.db_name)
    assert_true('name' in response.context["tables"][0])
    assert_true("test" in response.context["table_names"])

    # Should default to "default" database
    response = self.client.get("/metastore/tables/not_there")
    assert_equal(200, response.status_code)

    # And have detail
    response = self.client.get("/metastore/table/%s/test" % self.db_name)
    assert_true("foo" in response.content)
    assert_true("SerDe Library" in response.content, response.content)

    # Remember the number of history items. Use a generic fragment 'test' to pass verification.
    history_cnt = verify_history(self.client, fragment='test')

    # Show table data.
    response = self.client.get("/metastore/table/%s/test/read" % self.db_name, follow=True)
    response = self.client.get(reverse("beeswax:api_watch_query_refresh_json", kwargs={'id': response.context['query'].id}), follow=True)
    response = wait_for_query_to_finish(self.client, response, max=30.0)
    # Note that it may not return all rows at once. But we expect at least 10.
    results = fetch_query_result_data(self.client, response)
    assert_true(len(results['results']) > 0)
    # This should NOT go into the query history.
    assert_equal(verify_history(self.client, fragment='test'), history_cnt, 'Implicit queries should not be saved in the history')

  def test_show_tables(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    # Set max limit to 3
    resets = [
      HS2_GET_TABLES_MAX.set_for_testing(3)
    ]

    try:
      hql = """
        CREATE TABLE test_show_tables_1 (a int) COMMENT 'Test for show_tables';
        CREATE TABLE test_show_tables_2 (a int) COMMENT 'Test for show_tables';
        CREATE TABLE test_show_tables_3 (a int) COMMENT 'Test for show_tables';
      """
      resp = _make_query(self.client, hql, database=self.db_name)
      resp = wait_for_query_to_finish(self.client, resp, max=30.0)

      # Table should have been created
      response = self.client.get("/metastore/tables/%s?filter=show_tables" % self.db_name)
      assert_equal(200, response.status_code)
      assert_equal(len(response.context['tables']), 3)
      assert_equal(response.context['has_metadata'], True)
      assert_true('name' in response.context["tables"][0])
      assert_true('comment' in response.context["tables"][0])
      assert_true('type' in response.context["tables"][0])

      hql = """
        CREATE TABLE test_show_tables_4 (a int) COMMENT 'Test for show_tables';
        CREATE TABLE test_show_tables_5 (a int) COMMENT 'Test for show_tables';
      """
      resp = _make_query(self.client, hql, database=self.db_name)
      resp = wait_for_query_to_finish(self.client, resp, max=30.0)

      # Table should have been created
      response = self.client.get("/metastore/tables/%s?filter=show_tables" % self.db_name)
      assert_equal(200, response.status_code)
      assert_equal(len(response.context['tables']), 5)
      assert_equal(response.context['has_metadata'], False)
      assert_true('name' in response.context["tables"][0])
      assert_false('comment' in response.context["tables"][0], response.context["tables"])
      assert_false('type' in response.context["tables"][0])
    finally:
      for reset in resets:
        reset()

  def test_describe_view(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    resp = self.client.get('/metastore/table/%s/myview' % self.db_name)
    assert_equal(None, resp.context['sample'])
    assert_true(resp.context['table'].is_view)
    assert_true("View" in resp.content)
    assert_true("Drop View" in resp.content)
    # Breadcrumbs
    assert_true(self.db_name in resp.content)
    assert_true("myview" in resp.content)

  def test_describe_partitions(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    response = self.client.get("/metastore/table/%s/test_partitions" % self.db_name)
    assert_true("Show Partitions (2)" in response.content, response.content)

    response = self.client.get("/metastore/table/%s/test_partitions/partitions" % self.db_name, follow=True)
    assert_true("baz_one" in response.content)
    assert_true("boom_two" in response.content)
    assert_true("baz_foo" in response.content)
    assert_true("boom_bar" in response.content)
    # Breadcrumbs
    assert_true(self.db_name in response.content)
    assert_true("test_partitions" in response.content)
    assert_true("partitions" in response.content)

    # Not partitioned
    response = self.client.get("/metastore/table/%s/test/partitions" % self.db_name, follow=True)
    assert_true("is not partitioned." in response.content)

  def test_describe_partitioned_table_with_limit(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    # Limit to 90
    finish = BROWSE_PARTITIONED_TABLE_LIMIT.set_for_testing("90")
    try:
      response = self.client.get("/metastore/table/%s/test_partitions" % self.db_name)
      assert_true("0x%x" % 89 in response.content, response.content)
      assert_false("0x%x" % 90 in response.content, response.content)
    finally:
      finish()

  def test_read_partitions(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    partition_spec = "baz='baz_one',boom='boom_two'"
    response = self.client.get("/metastore/table/%s/test_partitions/partitions/%s/read" % (self.db_name, partition_spec), follow=True)
    response = self.client.get(reverse("beeswax:api_watch_query_refresh_json", kwargs={'id': response.context['query'].id}), follow=True)
    response = wait_for_query_to_finish(self.client, response, max=30.0)
    results = fetch_query_result_data(self.client, response)
    assert_true(len(results['results']) > 0, results)

  def test_browse_partition(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    partition_spec = "baz='baz_one',boom='boom_two'"
    response = self.client.get("/metastore/table/%s/test_partitions/partitions/%s/browse" % (self.db_name, partition_spec), follow=True)
    if is_live_cluster():
      path = '/user/hive/warehouse/%s.db/test_partitions/baz=baz_one/boom=boom_two' % self.db_name
    else:
      path = '/user/hive/warehouse/test_partitions/baz=baz_one/boom=boom_two'
    filebrowser_path = urllib.unquote(reverse("filebrowser.views.view", kwargs={'path': path}))
    assert_equal(response.request['PATH_INFO'], filebrowser_path)

  def test_drop_multi_tables(self):
    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    hql = """
      CREATE TABLE test_drop_1 (a int);
      CREATE TABLE test_drop_2 (a int);
      CREATE TABLE test_drop_3 (a int);
    """
    resp = _make_query(self.client, hql, database=self.db_name)
    resp = wait_for_query_to_finish(self.client, resp, max=30.0)

    # Drop them
    resp = self.client.get('/metastore/tables/drop/%s' % self.db_name, follow=True)
    assert_true('want to delete' in resp.content, resp.content)
    resp = self.client.post('/metastore/tables/drop/%s' % self.db_name, {u'table_selection': [u'test_drop_1', u'test_drop_2', u'test_drop_3']})
    assert_equal(resp.status_code, 302)


  def test_drop_multi_databases(self):
    db1 = '%s_test_drop_1' % self.db_name
    db2 = '%s_test_drop_2' % self.db_name
    db3 = '%s_test_drop_3' % self.db_name

    try:
      hql = """
        CREATE DATABASE %(db1)s;
        CREATE DATABASE %(db2)s;
        CREATE DATABASE %(db3)s;
      """ % {'db1': db1, 'db2': db2, 'db3': db3}
      resp = _make_query(self.client, hql)
      resp = wait_for_query_to_finish(self.client, resp, max=30.0)

      # Drop them
      resp = self.client.get('/metastore/databases/drop', follow=True)
      assert_true('want to delete' in resp.content, resp.content)
      resp = self.client.post('/metastore/databases/drop', {u'database_selection': [db1, db2, db3]})
      assert_equal(resp.status_code, 302)
    finally:
      make_query(self.client, 'DROP DATABASE IF EXISTS %(db)s' % {'db': db1}, wait=True)
      make_query(self.client, 'DROP DATABASE IF EXISTS %(db)s' % {'db': db2}, wait=True)
      make_query(self.client, 'DROP DATABASE IF EXISTS %(db)s' % {'db': db3}, wait=True)


  def test_load_data(self):
    """
    Test load data queries.
    These require Hadoop, because they ask the metastore
    about whether a table is partitioned.
    """

    if is_live_cluster():
      raise SkipTest('HUE-2902: Test is not re-entrant')

    # Check that view works
    resp = self.client.get("/metastore/table/%s/test/load" % self.db_name, follow=True)
    assert_true('Path' in resp.content)

    data_path = '%(prefix)s/tmp/foo' % {'prefix': self.cluster.fs_prefix}

    # Try the submission
    self.client.post("/metastore/table/%s/test/load" % self.db_name, {'path': data_path, 'overwrite': True}, follow=True)
    query = QueryHistory.objects.latest('id')

    assert_equal_mod_whitespace("LOAD DATA INPATH '%(data_path)s' OVERWRITE INTO TABLE `%(db)s`.`test`" % {'data_path': data_path, 'db': self.db_name}, query.query)

    resp = self.client.post("/metastore/table/%s/test/load" % self.db_name, {'path': data_path, 'overwrite': False}, follow=True)
    query = QueryHistory.objects.latest('id')
    assert_equal_mod_whitespace("LOAD DATA INPATH '%(data_path)s' INTO TABLE `%(db)s`.`test`" % {'data_path': data_path, 'db': self.db_name}, query.query)

    # Try it with partitions
    resp = self.client.post("/metastore/table/%s/test_partitions/load" % self.db_name, {'path': data_path, 'partition_0': "alpha", 'partition_1': "beta"}, follow=True)
    query = QueryHistory.objects.latest('id')
    assert_equal_mod_whitespace(query.query, "LOAD DATA INPATH '%(data_path)s' INTO TABLE `%(db)s`.`test_partitions` PARTITION (baz='alpha', boom='beta')" % {'data_path': data_path, 'db': self.db_name})


  def test_has_write_access_frontend(self):
    client = make_logged_in_client(username='write_access_frontend', groupname='write_access_frontend', is_superuser=False)
    grant_access("write_access_frontend", "write_access_frontend", "metastore")
    user = User.objects.get(username='write_access_frontend')

    def check(client, assertz):
      response = client.get("/metastore/databases")
      assertz("Drop</button>" in response.content, response.content)
      assertz("Create a new database" in response.content, response.content)

      response = client.get("/metastore/tables/")
      assertz("Drop</button>" in response.content, response.content)
      assertz("Create a new table" in response.content, response.content)

    check(client, assert_false)

    # Add access
    group, created = Group.objects.get_or_create(name='write_access_frontend')
    perm, created = HuePermission.objects.get_or_create(app='metastore', action='write')
    GroupPermission.objects.get_or_create(group=group, hue_permission=perm)

    check(client, assert_true)


  def test_has_write_access_backend(self):
    if is_live_cluster():
      raise SkipTest('HUE-2900: Needs debugging on live cluster')

    client = make_logged_in_client(username='write_access_backend', groupname='write_access_backend', is_superuser=False)
    grant_access("write_access_backend", "write_access_backend", "metastore")
    grant_access("write_access_backend", "write_access_backend", "beeswax")
    user = User.objects.get(username='write_access_backend')

    resp = _make_query(client, 'CREATE TABLE test_perm_1 (a int);', database=self.db_name) # Only fails if we were using Sentry and won't allow SELECT to user
    resp = wait_for_query_to_finish(client, resp, max=30.0)

    def check(client, http_codes):
      resp = client.get('/metastore/tables/drop/%s' % self.db_name)
      assert_true(resp.status_code in http_codes, resp.content)

      resp = client.post('/metastore/tables/drop/%s' % self.db_name, {u'table_selection': [u'test_perm_1']})
      assert_true(resp.status_code in http_codes, resp.content)

    check(client, [301]) # Denied

    # Add access
    group, created = Group.objects.get_or_create(name='write_access_backend')
    perm, created = HuePermission.objects.get_or_create(app='metastore', action='write')
    GroupPermission.objects.get_or_create(group=group, hue_permission=perm)

    check(client, [200, 302]) # Ok


class TestParser(object):

  def test_parse_simple(self):
    name = 'simple'
    type = 'string'
    comment = 'test_parse_simple'
    column = {'name': name, 'type': type, 'comment': comment}
    parse_tree = parser.parse_column(name, type, comment)
    assert_equal(parse_tree, column)


  def test_parse_array(self):
    name = 'array'
    type = 'array<string>'
    comment = 'test_parse_array'
    column = {'name': name, 'type': 'array', 'comment': comment, 'item': {'type': 'string'}}
    parse_tree = parser.parse_column(name, type, comment)
    assert_equal(parse_tree, column)


  def test_parse_map(self):
    name = 'map'
    type = 'map<string,int>'
    comment = 'test_parse_map'
    column = {'name': name, 'type': 'map', 'comment': comment, 'key': {'type': 'string'}, 'value': {'type': 'int'}}
    parse_tree = parser.parse_column(name, type, comment)
    assert_equal(parse_tree, column)


  def test_parse_struct(self):
    name = 'struct'
    type = 'struct<name:string,age:int>'
    comment = 'test_parse_struct'
    column = {'name': name, 'type': 'struct', 'comment': comment, 'fields': [{'name': 'name', 'type': 'string'}, {'name': 'age', 'type': 'int'}]}
    parse_tree = parser.parse_column(name, type, comment)
    assert_equal(parse_tree, column)


  def test_parse_nested(self):
    name = 'nested'
    type = 'array<struct<name:string,age:int>>'
    comment = 'test_parse_nested'
    column = {'name': name, 'type': 'array', 'comment': comment, 'item': {'type': 'struct', 'fields': [{'name': 'name', 'type': 'string'}, {'name': 'age', 'type': 'int'}]}}
    parse_tree = parser.parse_column(name, type, comment)
    assert_equal(parse_tree, column)

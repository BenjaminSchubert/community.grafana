#!/usr/bin/python
# -*- coding: utf-8 -*-
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible. If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = """
---
module: grafana_contact_point
author:
  - Moritz Pötschk (@nemental)
version_added: "1.9.0"
short_description: Manage Grafana Contact Points
description:
  - Create/Update/Delete Grafana Contact Points via API.

extends_documentation_fragment:
  - community.grafana.basic_auth
  - community.grafana.api_key
"""


EXAMPLES = """
- name: Create email contact point
  community.grafana.grafana_contact_point:
    grafana_url: "{{ grafana_url }}"
    grafana_user: "{{ grafana_username }}"
    grafana_password: "{{ grafana_password }}"
    uid: email
    name: E-Mail
    type: email
    email_addresses:
      - example@example.com

- name: Delete email contact point
  community.grafana.grafana_contact_point:
    grafana_url: "{{ grafana_url }}"
    grafana_user: "{{ grafana_username }}"
    grafana_password: "{{ grafana_password }}"
    uid: email
    state: absent
"""

RETURN = """
contact_point:
  description: Contact point created or updated by the module.
  returned: changed
"""

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url
from ansible.module_utils._text import to_text
from ansible_collections.community.grafana.plugins.module_utils.base import (
    grafana_argument_spec,
    clean_url,
)
from ansible.module_utils.urls import basic_auth_header


class GrafanaAPIException(Exception):
    pass


def grafana_contact_point_payload_email(data, payload):
    payload["settings"]["addresses"] = ";".join(data["email_addresses"])
    if data.get("email_single"):
        payload["settings"]["singleEmail"] = data["email_single"]


def grafana_contact_point_payload(data):
    payload = {
        "uid": data["uid"],
        "name": data["name"],
        "type": data["type"],
        "isDefault": data["is_default"],
        "disableResolveMessage": data["disable_resolve_message"],
        "settings": {"uploadImage": data["include_image"]},
    }

    if data["type"] == "email":
        grafana_contact_point_payload_email(data, payload)

    return payload


class GrafanaContactPointInterface(object):
    def __init__(self, module):
        self._module = module
        self.org_id = None
        # {{{ Authentication header
        self.headers = {"Content-Type": "application/json"}
        if module.params.get("grafana_api_key", None):
            self.headers["Authorization"] = (
                "Bearer %s" % module.params["grafana_api_key"]
            )
        else:
            self.headers["Authorization"] = basic_auth_header(
                module.params["url_username"], module.params["url_password"]
            )
            self.org_id = (
                self.grafana_organization_by_name(module.params["org_name"])
                if module.params["org_name"]
                else module.params["org_id"]
            )
            self.grafana_switch_organisation(module.params, self.org_id)
        # }}}

    def grafana_organization_by_name(self, data, org_name):
        r, info = fetch_url(
            self._module,
            "%s/api/user/orgs" % data["url"],
            headers=self.headers,
            method="GET",
        )
        organizations = json.loads(to_text(r.read()))
        orga = next((org for org in organizations if org["name"] == org_name))
        if orga:
            return orga["orgId"]

        raise GrafanaAPIException(
            "Current user isn't member of organization: %s" % org_name
        )

    def grafana_switch_organisation(self, data, org_id):
        r, info = fetch_url(
            self._module,
            "%s/api/user/using/%s" % (data["url"], org_id),
            headers=self.headers,
            method="POST",
        )
        if info["status"] != 200:
            raise GrafanaAPIException(
                "Unable to switch to organization %s : %s" % (org_id, info)
            )

    def grafana_check_contact_point_match(self, data):
        r, info = fetch_url(
            self._module,
            "%s/api/v1/provisioning/contact-points" % data["url"],
            headers=self.headers,
            method="GET",
        )

        if info["status"] == 200:
            contact_points = json.loads(to_text(r.read()))
            before = next(
                (cp for cp in contact_points if cp["uid"] == data["uid"]), None
            )
            return self.grafana_handle_contact_point(data, before)
        else:
            raise GrafanaAPIException(
                "Unable to get contact point %s : %s" % (data["uid"], info)
            )

    def grafana_handle_contact_point(self, data, before):
        payload = grafana_contact_point_payload(data)

        if data["state"] == "present":
            if before:
                return self.grafana_update_contact_point(data, payload, before)
            else:
                return self.grafana_create_contact_point(data, payload)
        else:
            if before:
                return self.grafana_delete_contact_point(data)
            else:
                return {"changed": False}

    def grafana_create_contact_point(self, data, payload):
        r, info = fetch_url(
            self._module,
            "%s/api/v1/provisioning/contact-points" % data["url"],
            data=json.dumps(payload),
            headers=self.headers,
            method="POST",
        )

        if info["status"] == 202:
            contact_point = json.loads(to_text(r.read()))
            return {
                "changed": True,
                "state": data["state"],
                "contact_point": contact_point,
            }
        else:
            raise GrafanaAPIException("Unable to create contact point: %s" % info)

    def grafana_update_contact_point(self, data, payload, before):
        r, info = fetch_url(
            self._module,
            "%s/api/v1/provisioning/contact-points/%s" % (data["url"], data["uid"]),
            data=json.dumps(payload),
            headers=self.headers,
            method="PUT",
        )

        if info["status"] == 202:
            contact_point = json.loads(to_text(r.read()))

            if before == contact_point:
                return {"changed": False}
            else:
                return {
                    "changed": True,
                    "diff": {"before": before, "after": payload},
                    "contact_point": contact_point,
                }
        else:
            raise GrafanaAPIException(
                "Unable to update contact point %s : %s" % (data["uid"], info)
            )

    def grafana_delete_contact_point(self, data):
        r, info = fetch_url(
            self._module,
            "%s/api/v1/provisioning/contact-points/%s" % (data["url"], data["uid"]),
            headers=self.headers,
            method="DELETE",
        )

        if info["status"] == 202:
            return {"state": "absent", "changed": True}
        elif info["status"] == 404:
            return {"changed": False}
        else:
            raise GrafanaAPIException(
                "Unable to delete contact point %s : %s" % (data["uid"], info)
            )


def main():
    argument_spec = grafana_argument_spec()
    argument_spec.update(
        org_id=dict(type="int", default=1),
        org_name=dict(type="str"),
        uid=dict(type="str"),
        name=dict(type="str"),
        type=dict(type="str", choices=["email"]),
        is_default=dict(type="bool", default=False),
        include_image=dict(type="bool", default=False),
        disable_resolve_message=dict(type="bool", default=False),
        email_addresses=dict(type="list", elements="str"),
        email_single=dict(type="bool"),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=False,
        required_together=[["url_username", "url_password", "org_id"]],
        mutually_exclusive=[["url_username", "grafana_api_key"]],
        required_if=[
            ["state", "present", ["name", "type"]],
            ["type", "email", ["email_addresses"]],
        ],
    )

    module.params["url"] = clean_url(module.params["url"])
    grafana_iface = GrafanaContactPointInterface(module)

    result = grafana_iface.grafana_check_contact_point_match(module.params)
    module.exit_json(failed=False, **result)


if __name__ == "__main__":
    main()

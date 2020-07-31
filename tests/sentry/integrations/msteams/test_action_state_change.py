from __future__ import absolute_import

import responses
import time

from sentry.utils.compat.mock import patch

from sentry.models import (
    AuthProvider,
    AuthIdentity,
    Integration,
    OrganizationIntegration,
    Identity,
    IdentityProvider,
    IdentityStatus,
    Group,
    GroupAssignee,
    GroupStatus,
)
from sentry.testutils import APITestCase
from sentry.utils import json
from sentry.integrations.msteams.utils import build_linking_card
from sentry.integrations.msteams.link_identity import build_linking_url


class BaseEventTest(APITestCase):
    def setUp(self):
        super(BaseEventTest, self).setUp()
        self.user = self.create_user(is_superuser=False)
        self.org = self.create_organization(owner=None)
        self.team = self.create_team(organization=self.org, members=[self.user])

        self.integration = Integration.objects.create(
            provider="msteams",
            name="Fellowship of the Ring",
            external_id="f3ll0wsh1p",
            metadata={
                "service_url": "https://smba.trafficmanager.net/amer",
                "access_token": "y0u_5h4ll_n07_p455",
                "expires_at": int(time.time()) + 86400,
            },
        )
        OrganizationIntegration.objects.create(organization=self.org, integration=self.integration)

        self.idp = IdentityProvider.objects.create(
            type="msteams", external_id="f3ll0wsh1p", config={}
        )
        self.identity = Identity.objects.create(
            external_id="g4nd4lf",
            idp=self.idp,
            user=self.user,
            status=IdentityStatus.VALID,
            scopes=[],
        )

        self.project1 = self.create_project(organization=self.org)
        self.group1 = self.create_group(project=self.project1)

    def post_webhook(
        self,
        action_type=None,
        user_id="g4nd4lf",
        team_id="f3ll0wsh1p",
        tenant_id="m17hr4nd1r",
        group_id=None,
        resolve_input=None,
        ignore_input=None,
        assign_input=None,
    ):
        payload = {
            "type": "message",
            "from": {"id": user_id},
            "channelData": {"team": {"id": team_id}, "tenant": {"id": tenant_id}},
            "value": {
                "groupId": group_id or self.group1.id,
                "actionType": action_type,
                "resolveInput": resolve_input,
                "ignoreInput": ignore_input,
                "assignInput": assign_input,
            },
        }

        return self.client.post("/extensions/msteams/webhook/", data=payload)


class StatusActionTest(BaseEventTest):
    @patch("sentry.integrations.msteams.link_identity.sign")
    @patch("sentry.integrations.msteams.webhook.verify_signature")
    @responses.activate
    def test_ask_linking(self, sign, verify):
        sign.return_value = "signed_parameters"
        verify.return_value = True

        def user_conversation_id_callback(request):
            payload = json.loads(request.body)
            if payload["members"] == [{"id": "s4ur0n"}] and payload["channelData"] == {
                "tenant": {"id": "7h3_gr347"}
            }:
                return (200, {}, json.dumps({"id": "d4rk_l0rd"}))
            return (404, {}, json.dumps({}))

        responses.add_callback(
            method=responses.POST,
            url="https://smba.trafficmanager.net/amer/v3/conversations",
            callback=user_conversation_id_callback,
        )

        responses.add(
            method=responses.POST,
            url="https://smba.trafficmanager.net/amer/v3/conversations/d4rk_l0rd/activities",
            status=200,
            json={},
        )

        resp = self.post_webhook(user_id="s4ur0n", tenant_id="7h3_gr347")

        linking_url = build_linking_url(
            self.integration, self.org, "s4ur0n", "f3ll0wsh1p", "7h3_gr347"
        )

        data = json.loads(responses.calls[1].request.body)

        assert resp.status_code == 201
        assert "attachments" in data
        assert data["attachments"][0]["content"] == build_linking_card(linking_url)

    @patch("sentry.integrations.msteams.webhook.verify_signature")
    def test_ignore_issue(self, verify):
        verify.return_value = True
        resp = self.post_webhook(action_type="ignore", ignore_input="-1")
        self.group1 = Group.objects.get(id=self.group1.id)

        assert resp.status_code == 200, resp.content
        assert self.group1.get_status() == GroupStatus.IGNORED

    @patch("sentry.integrations.msteams.webhook.verify_signature")
    def test_ignore_issue_with_additional_user_auth(self, verify):
        verify.return_value = True
        auth_idp = AuthProvider.objects.create(organization=self.org, provider="nobody")
        AuthIdentity.objects.create(auth_provider=auth_idp, user=self.user)

        resp = self.post_webhook(action_type="ignore", ignore_input="-1")
        self.group1 = Group.objects.get(id=self.group1.id)

        assert resp.status_code == 200, resp.content
        assert self.group1.get_status() == GroupStatus.IGNORED

    @patch("sentry.integrations.msteams.webhook.verify_signature")
    def test_assign_to_team(self, verify):
        verify.return_value = True
        resp = self.post_webhook(action_type="assign", assign_input=u"team:{}".format(self.team.id))

        assert resp.status_code == 200, resp.content
        assert GroupAssignee.objects.filter(group=self.group1, team=self.team).exists()

    @patch("sentry.integrations.msteams.webhook.verify_signature")
    def test_assign_to_me(self, verify):
        verify.return_value = True
        resp = self.post_webhook(action_type="assign", assign_input="ME")

        assert resp.status_code == 200, resp.content
        assert GroupAssignee.objects.filter(group=self.group1, user=self.user).exists()

    @patch("sentry.integrations.msteams.webhook.verify_signature")
    def test_assign_to_me_multiple_identities(self, verify):
        verify.return_value = True
        org2 = self.create_organization(owner=None)

        integration2 = Integration.objects.create(
            provider="msteams",
            name="Army of Mordor",
            external_id="54rum4n",
            metadata={
                "service_url": "https://smba.trafficmanager.net/amer",
                "access_token": "y0u_h4v3_ch053n_d347h",
                "expires_at": int(time.time()) + 86400,
            },
        )
        OrganizationIntegration.objects.create(organization=org2, integration=integration2)

        idp2 = IdentityProvider.objects.create(type="msteams", external_id="54rum4n", config={})
        Identity.objects.create(
            external_id="7h3_gr3y",
            idp=idp2,
            user=self.user,
            status=IdentityStatus.VALID,
            scopes=[],
        )

        resp = self.post_webhook(action_type="assign", assign_input="ME")

        assert resp.status_code == 200, resp.content
        assert GroupAssignee.objects.filter(group=self.group1, user=self.user).exists()

    @patch("sentry.integrations.msteams.webhook.verify_signature")
    def test_resolve_issue(self, verify):
        verify.return_value = True
        resp = self.post_webhook(action_type="resolve", resolve_input="resolved")
        self.group1 = Group.objects.get(id=self.group1.id)

        assert resp.status_code == 200, resp.content
        assert self.group1.get_status() == GroupStatus.RESOLVED

    @patch("sentry.integrations.msteams.webhook.verify_signature")
    def test_no_integration(self, verify):
        verify.return_value = True
        self.integration.delete()
        resp = self.post_webhook()
        assert resp.status_code == 404
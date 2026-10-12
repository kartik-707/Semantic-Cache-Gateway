import unittest

from pydantic import ValidationError

from contracts.policy import PolicyConfig
from contracts.proxy import Message, ProxyRequest
from gateway.cache_rules import build_context


class CacheRuleTests(unittest.TestCase):
    def setUp(self):
        self.policy = PolicyConfig(
            feature_name="faq_bot",
            similarity_threshold=0.92,
            ttl_seconds=3600,
            cache_enabled=True,
            tags=[],
            user_scoped=False,
            judge_required_above=0.85,
            disable_for_tags=[],
        )

        self.request = ProxyRequest(
            model="test-model",
            messages=[
                Message(role="system", content="Answer using our store policy."),
                Message(role="user", content="What is your return policy?"),
            ],
            feature_name="faq_bot",
        )

    def context(self, request=None, policy=None):
        return build_context(
            request or self.request,
            policy or self.policy,
        )

    def test_paraphrases_share_compatibility_but_not_exact_key(self):
        changed = self.request.model_copy(update={
            "messages": [
                self.request.messages[0],
                Message(role="user", content="How can I return an item?"),
            ]
        })

        original = self.context()
        paraphrase = self.context(changed)

        self.assertEqual(
            original.compatibility_key,
            paraphrase.compatibility_key,
        )
        self.assertNotEqual(original.exact_key, paraphrase.exact_key)

    def test_users_and_anonymous_requests_are_separate(self):
        contexts = [
            self.context(self.request.model_copy(update={"user_id": user}))
            for user in [None, "alice", "bob"]
        ]

        self.assertEqual(
            len({context.compatibility_key for context in contexts}),
            3,
        )
        self.assertEqual(
            len({context.exact_key for context in contexts}),
            3,
        )

    def test_changed_settings_cannot_reuse_cache(self):
        baseline = self.context()

        changes = [
            {"model": "other-model"},
            {"temperature": 0.1},
            {"max_tokens": 50},
            {"session_id": "another-session"},
            {"tags": ["creative"]},
            {
                "messages": [
                    Message(role="system", content="Different instructions."),
                    self.request.messages[-1],
                ]
            },
        ]

        for change in changes:
            with self.subTest(change=change):
                changed = self.context(
                    self.request.model_copy(update=change)
                )
                self.assertNotEqual(
                    baseline.compatibility_key,
                    changed.compatibility_key,
                )
                self.assertNotEqual(
                    baseline.exact_key,
                    changed.exact_key,
                )

    def test_private_request_without_identity_bypasses_cache(self):
        private_policy = self.policy.model_copy(update={
            "user_scoped": True,
        })

        self.assertFalse(
            self.context(policy=private_policy).enabled
        )

        tagged_request = self.request.model_copy(update={
            "tags": ["user_specific"],
        })

        self.assertFalse(self.context(tagged_request).enabled)

    def test_request_and_policy_disable_cache(self):
        disabled_request = self.request.model_copy(update={
            "cache_disabled": True,
        })
        disabled_policy = self.policy.model_copy(update={
            "cache_enabled": False,
        })

        self.assertFalse(self.context(disabled_request).enabled)
        self.assertFalse(self.context(policy=disabled_policy).enabled)

    def test_policy_tags_are_applied_without_request_tags(self):
        news_policy = self.policy.model_copy(update={
            "tags": ["current_events"],
            "disable_for_tags": ["current_events"],
        })

        self.assertFalse(self.context(policy=news_policy).enabled)

    def test_ttl_override_can_shorten_but_not_extend_policy(self):
        shorter = self.request.model_copy(update={
            "cache_ttl_override": 60,
        })
        longer = self.request.model_copy(update={
            "cache_ttl_override": 10000,
        })

        self.assertEqual(self.context(shorter).ttl_seconds, 60)
        self.assertEqual(self.context(longer).ttl_seconds, 3600)

    def test_policy_change_invalidates_compatibility(self):
        changed_policy = self.policy.model_copy(update={
            "similarity_threshold": 0.97,
        })

        self.assertNotEqual(
            self.context().compatibility_key,
            self.context(policy=changed_policy).compatibility_key,
        )

    def test_feature_name_cannot_escape_policy_directory(self):
        data = self.request.model_dump()
        data["feature_name"] = "../secret"

        with self.assertRaises(ValidationError):
            ProxyRequest.model_validate(data)

    def test_empty_message_list_is_rejected(self):
        data = self.request.model_dump()
        data["messages"] = []

        with self.assertRaises(ValidationError):
            ProxyRequest.model_validate(data)


if __name__ == "__main__":
    unittest.main()
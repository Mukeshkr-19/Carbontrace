import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
ACCOUNT_ID = "562325340670"
REGION = "us-east-1"
DEPLOYMENT_POLICY_PATH = ROOT / "bootstrap" / "main-deployment-policy.json"


def load_policy(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text())


def statements_with_action(policy: dict, action: str) -> list[dict]:
    matches = []
    for statement in policy["Statement"]:
        actions = statement["Action"]
        if isinstance(actions, str):
            actions = [actions]
        if action in actions:
            matches.append(statement)
    return matches


class IamPolicyRegressionTests(unittest.TestCase):
    def test_create_security_group_splits_tagged_group_from_existing_vpc(self) -> None:
        policy = load_policy("bootstrap/main-deployment-policy.json")
        statements = statements_with_action(policy, "ec2:CreateSecurityGroup")
        self.assertEqual(len(statements), 2)

        security_group_arn = f"arn:aws:ec2:{REGION}:{ACCOUNT_ID}:security-group/*"
        vpc_arn = f"arn:aws:ec2:{REGION}:{ACCOUNT_ID}:vpc/*"
        tagged_statement = next(
            statement for statement in statements if security_group_arn in statement["Resource"]
        )
        vpc_statement = next(
            statement for statement in statements if vpc_arn in statement["Resource"]
        )
        self.assertEqual(
            tagged_statement["Condition"]["StringEquals"]["aws:RequestTag/Project"],
            "Carbontrace",
        )
        self.assertNotIn("Condition", vpc_statement)

    def test_log_group_permissions_cover_exact_and_stream_arns_and_tagging(self) -> None:
        policy = load_policy("bootstrap/main-deployment-policy.json")
        exact_arns = {
            f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/carbontrace/carbontrace",
            f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/lambda/carbontrace-auto-stop",
        }
        stream_arns = {f"{arn}:*" for arn in exact_arns}

        create_statement = statements_with_action(policy, "logs:CreateLogGroup")[0]
        self.assertTrue(exact_arns.issubset(set(create_statement["Resource"])))
        self.assertTrue(stream_arns.issubset(set(create_statement["Resource"])))

        tag_statement = statements_with_action(policy, "logs:TagResource")[0]
        self.assertTrue(exact_arns.issubset(set(tag_statement["Resource"])))
        self.assertTrue(stream_arns.issubset(set(tag_statement["Resource"])))

    def test_pass_role_is_exactly_scoped_by_role_and_service(self) -> None:
        policy = load_policy("bootstrap/main-deployment-policy.json")
        statements = statements_with_action(policy, "iam:PassRole")
        self.assertEqual(len(statements), 2)
        grants = {
            (
                statement["Resource"],
                statement["Condition"]["StringEquals"]["iam:PassedToService"],
            )
            for statement in statements
        }
        self.assertEqual(
            grants,
            {
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/carbontrace-ec2-role",
                    "ec2.amazonaws.com",
                ),
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/carbontrace-auto-stop-role",
                    "lambda.amazonaws.com",
                ),
            },
        )

    def test_compact_deployment_policy_stays_below_safety_budget(self) -> None:
        policy = json.loads(DEPLOYMENT_POLICY_PATH.read_text())
        compact = json.dumps(policy, separators=(",", ":"))
        self.assertLessEqual(len(compact), 5_900)

    def test_no_unreviewed_wildcard_actions_or_mutating_wildcard_resources(self) -> None:
        permission_paths = [
            "bootstrap/backend-access-policy.json",
            "bootstrap/deployment-user-policy.json",
            "bootstrap/main-deployment-policy.json",
            "bootstrap/runtime-roles/ec2-permissions-policy.json",
            "bootstrap/runtime-roles/lambda-permissions-policy.json",
        ]
        reviewed_wildcard_mutations = {
            (
                "bootstrap/runtime-roles/ec2-permissions-policy.json",
                "PublishOnlyCarbontraceMetrics",
                "cloudwatch:PutMetricData",
            )
        }
        read_verbs = ("Describe", "Get", "List")

        for relative_path in permission_paths:
            policy = load_policy(relative_path)
            for statement in policy["Statement"]:
                actions = statement["Action"]
                if isinstance(actions, str):
                    actions = [actions]
                self.assertNotIn("*", actions, relative_path)
                resources = statement.get("Resource", [])
                if isinstance(resources, str):
                    resources = [resources]
                if "*" not in resources:
                    continue
                for action in actions:
                    verb = action.split(":", 1)[1]
                    if verb.startswith(read_verbs):
                        continue
                    reviewed = (relative_path, statement.get("Sid"), action)
                    self.assertIn(reviewed, reviewed_wildcard_mutations)
                    self.assertEqual(
                        statement["Condition"]["StringEquals"]["cloudwatch:namespace"],
                        ["Carbontrace/App", "Carbontrace/Host"],
                    )

    def test_auto_stop_and_lease_permissions_are_narrow(self) -> None:
        lambda_policy = load_policy(
            "bootstrap/runtime-roles/lambda-permissions-policy.json"
        )
        describe = statements_with_action(lambda_policy, "ec2:DescribeInstances")
        self.assertEqual(len(describe), 1)
        self.assertEqual(describe[0]["Resource"], "*")
        self.assertEqual(
            describe[0]["Condition"]["StringEquals"]["aws:RequestedRegion"], REGION
        )
        self.assertEqual(
            statements_with_action(lambda_policy, "ec2:TerminateInstances"), []
        )
        stop = statements_with_action(lambda_policy, "ec2:StopInstances")[0]
        self.assertEqual(
            stop["Condition"]["StringEquals"],
            {
                "ec2:ResourceTag/Name": "carbontrace-profiler",
                "ec2:ResourceTag/Project": "Carbontrace",
            },
        )

        ec2_policy = load_policy("bootstrap/runtime-roles/ec2-permissions-policy.json")
        ec2_describe = statements_with_action(ec2_policy, "ec2:DescribeInstances")
        self.assertEqual(len(ec2_describe), 1)
        self.assertEqual(ec2_describe[0]["Resource"], "*")
        self.assertEqual(
            ec2_describe[0]["Condition"]["StringEquals"]["aws:RequestedRegion"],
            REGION,
        )
        lease = statements_with_action(ec2_policy, "ec2:CreateTags")[0]
        self.assertEqual(
            lease["Action"], ["ec2:CreateTags", "ec2:DeleteTags"]
        )
        self.assertEqual(
            lease["Condition"]["ForAllValues:StringEquals"]["aws:TagKeys"],
            ["CarbontraceActiveUntil"],
        )
        self.assertEqual(
            lease["Condition"]["StringEquals"]["ec2:ResourceTag/Name"],
            "carbontrace-profiler",
        )
        self.assertEqual(
            lease["Condition"]["StringEquals"]["ec2:ResourceTag/Project"],
            "Carbontrace",
        )


if __name__ == "__main__":
    unittest.main()

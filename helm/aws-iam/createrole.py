#!/usr/bin/env python3
import json
import pathlib
from sre_parse import State
import yaml
import subprocess
import argparse

DEFAULT_WORKING_DIR = "./tmp/"

ROLE_TEMPLATE = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "sts:AssumeRole",
            "Principal": {"AWS": []},
            "Condition": {},
        }
    ],
}


POLICY_TEMPLATE = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowAssumeOrganizationAccountRole",
            "Effect": "Allow",
            "Action": "sts:AssumeRole",
            "Resource": None,
        }
    ],
}

AWS_ACCOUNT_ID = None
ARGS = None


def run_command(cmd, **kwargs):
    "Passes args to subprocess.run, printing out the command before running it"
    print("Running:", " ".join(cmd))
    return subprocess.run(cmd, **kwargs)


def get_aws_account_id():
    """Account ID for the current aws cli configuration, caching where possible"""
    global AWS_ACCOUNT_ID
    if not AWS_ACCOUNT_ID:
        run = run_command(
            ["aws", "sts", "get-caller-identity"],
            check=True,
            capture_output=True,
            text=True,
        )
        AWS_ACCOUNT_ID = json.loads(run.stdout)["Account"]
    return AWS_ACCOUNT_ID


def create_aws_role(rolename):
    """Create an AWS role of the given rolename, with the current account as the principal"""
    userarns = [
        f"arn:aws:iam::{get_aws_account_id()}:user/{username}"
        for username in ARGS.usernames
    ]
    ROLE_TEMPLATE["Statement"][0]["Principal"]["AWS"] = userarns
    rolef = ARGS.workingdir / "role_policy.json"
    json.dump(ROLE_TEMPLATE, rolef.open("w"))
    run_command(
        [
            "aws",
            "iam",
            "create-role",
            "--role-name",
            rolename,
            "--assume-role-policy-document",
            f"file://{str(rolef)}",
        ],
    )


def XXXcreate_aws_policy(policyname, rolename):
    "Create <policyname> to allow <rolename> to be assumed"
    POLICY_TEMPLATE["Statement"][0][
        "Resource"
    ] = f"arn:aws:iam::{get_aws_account_id()}:role/{rolename}"
    policyf = ARGS.workingdir / "assume_role_policy.json"
    json.dump(POLICY_TEMPLATE, policyf.open("w"))
    print(policyf)
    # run_command(
    #     [
    #         "aws",
    #         "iam",
    #         "create-policy",
    #         "--policy-name",
    #         policyname,
    #         "--policy-document",
    #         f"file://{str(policyf)}",
    #     ]
    # )


def create_aws_group(groupname, rolename, policyname):
    """Create <groupname>, and give it the policy <policyname>, allowing assume role.

    Users are then assigned to the group.
    """
    run_command(["aws", "iam", "create-group", "--group-name", groupname])

    POLICY_TEMPLATE["Statement"][0][
        "Resource"
    ] = f"arn:aws:iam::{get_aws_account_id()}:role/{rolename}"
    policyf = ARGS.workingdir / "assume_role_policy.json"
    json.dump(POLICY_TEMPLATE, policyf.open("w"))
    run_command(
        [
            "aws",
            "iam",
            "put-group-policy",
            "--group-name",
            groupname,
            "--policy-name",
            policyname,
            "--policy-document",
            f"file://{str(policyf)}",
        ]
    )

    # Assign users
    for username in ARGS.usernames:
        run_command(
            [
                "aws",
                "iam",
                "add-user-to-group",
                "--group-name",
                groupname,
                "--user-name",
                username,
            ]
        )


def create_identity_mapping(rolename, eksusername):
    """Creates an identity mapping from k8s to the AWS IAM"""
    run_command(
        [
            "eksctl",
            "create",
            "iamidentitymapping",
            "--cluster",
            ARGS.clustername,
            "--arn",
            f"arn:aws:iam::{get_aws_account_id()}:role/{rolename}",
            "--username",
            eksusername,
        ]
    )


def getargs():
    parser = argparse.ArgumentParser(
        description="Create a AWS role and group, and link to k8s. The currently "
        "active aws cli and kubectl context is used. Names and roles are prefixes "
        'with "eks-" and finish with their purpose'
    )
    parser.add_argument(
        "accountname",
        help="The base name of the account/role/group to create",
    )
    parser.add_argument(
        "--clustername",
        "-c",
        required=True,
        help="The name of the EKS cluster",
    )
    parser.add_argument(
        "--region",
        "-r",
        help="The region of EKS cluster, if not set, uses same as aws cli",
    )
    parser.add_argument(
        "--usernames",
        "-u",
        required=True,
        action="append",
        help="Usernames of users to add to this role. This is just the username, not the full ARN",
    )
    parser.add_argument(
        "--workingdir",
        default=DEFAULT_WORKING_DIR,
        help="Directory for placing files generated during the process for the aws cli",
    )
    args = parser.parse_args()

    args.workingdir = pathlib.Path(args.workingdir)
    args.workingdir.mkdir(exist_ok=True, parents=True)

    for username in args.usernames:
        if ":" in username or "/" in username:
            parser.error(
                f"Username only should be passed, not a full ARN (f{username})"
            )
    return args


def main():
    global ARGS
    ARGS = getargs()

    rolename = f"eks-{ARGS.accountname}-role"
    policyname = f"eks-{ARGS.accountname}-assume-role-policy"
    groupname = f"eks-{ARGS.accountname}-group"
    eksusername = f"eks-{ARGS.accountname}"
    # create_aws_role(rolename)
    # create_aws_policy(policyname, rolename)
    # create_aws_group(groupname, rolename, policyname)
    create_identity_mapping(rolename, eksusername)


if __name__ == "__main__":
    main()

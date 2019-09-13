# pyGithubMigrator

### Prerequisites

    - python3.6 or above
    - python requests >= 2.22.0 
        `pip install requests`

### Steps to follow
- Create user and personal access tokens in source and target GitHub. Also user should have write privileges to source and target organization.
- Create Organization in target GitHub. Alternatively you can pass user with site admin privileges as an argument to automatically create organization in target GitHub

### Actions performed
- Clone description, data, branches and tags from all repos
- Create pull requests as per source repos
- Send invitations to all users who are added in source organization
- Create teams and send invitations to members as per source organization
- Clone all pull and commit comments
- Add assignees and reviewers as per details in source repos

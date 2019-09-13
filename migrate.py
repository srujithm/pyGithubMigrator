import json
import argparse
import requests
import logging
import sys
import tempfile
import os
import subprocess
from datetime import datetime

# Normalize received user input
remove_trailing_slash = lambda url: url[:-1] if url.endswith("/") else url
normalize_url = lambda url: remove_trailing_slash(url) if "http" in url else "https://{}".format(remove_trailing_slash(url))
api_url = lambda url: "{}/api/v3".format(url)
fetch_url_from_api = lambda url: url.replace("/api/v3","").replace("https://","")
create_headers = lambda token: {"Authorization" : "token {}".format(token)}

def get_args():
    parser = argparse.ArgumentParser(description="Migrate GitHub Orginazation")
    parser.add_argument("--source-url", help="Provide source GitHub url", required=True)
    parser.add_argument("--source-org", help="Provide source organization name", required=True)
    parser.add_argument("--target-url", help="provide target GitHub url", required=True)
    parser.add_argument("--target-org", help="Provide target organization name, if not provided target_org will be created using source org", required=False)
    parser.add_argument("--user", help="Provide user used to create source and target tokens", required=True)
    parser.add_argument("--repos",help="Migrate only desired repos, provide repo names as space seperated values", required=False, nargs="+")
    parser.add_argument("--source-token", help="Provide token to connect to source GitHub", required=True)
    parser.add_argument("--target-token", help="Provide token to connect to source GitHub", required=True)
    parser.add_argument("--site-admin", help="Provide Github site administartor details to automatically create Organizations", required=False)
    args = parser.parse_args()
    return args

args = get_args()
source_url = normalize_url(args.source_url)
target_url = normalize_url(args.target_url)
source_api_url = api_url(source_url)
target_api_url = api_url(target_url)
source_org = args.source_org
target_org = source_org if args.target_org is None else args.target_org
source_token = args.source_token
target_token = args.target_token
source_headers = create_headers(source_token)
target_headers = create_headers(target_token)
user = args.user

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s',level=logging.INFO, datefmt='%d/%m/%Y %I:%M:%S')
logger = logging.getLogger(__name__)

class Repo():
    def __init__(self, name, private, description=None):
        self.name = name
        self.private = private
        self.description = description
    
    def __str__(self):
        return self.name

class Team():
    def __init__(self, name, description, privacy, repos, members, maintainers, ldap_dn):
        self.name = name
        self.description =description
        self.privacy = privacy
        self.repo_names = repos
        self.members = members
        self.maintainers = maintainers
        if ldap_dn != "":
            self.ldap_dn = ldap_dn
    
    def __str__(self):
        return self.name

class User():
    def __init__(self,login,id):
        self.login = login
        self.id = id

class pull_request():
    def __init__(self, number, user, title, body, created, head, base, assignees, reviewers, reviews, review_comments, comments):
        self.number = number
        self.title = title
        self.body = "Originally created as #{number} by [{user}]({url}/{user}) on {created} \r\n\r\n {body}".format(number=number, user=user, created=created, body=body, url=target_url)
        self.head = head
        self.base = base
        self.assignees = assignees
        self.reviewers = reviewers
        self.reviews = reviews
        self.review_comments = review_comments
        self.comments = comments


    def __str__(self):
        return self.title

class Comment():
    def __init__(self, user, body, created):
        self.body = "Originally created by [{user}]({url}/{user}) on {created} \r\n\r\n {body}".format(user=user, created=created, body=body, url=target_url)
        self.created = created # datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")

    def __str__(self):
        return self.body

class ReviewComment(Comment):
    def __init__(self, user, body, created, commit_id, position, path):
        super().__init__(user, body, created)
        self.commit_id = commit_id
        self.path = path
        self.position = position
    
    def __str__(self):
        return self.body

class Review(Comment):
    def __init__(self, user, body, created, event):
        super().__init__(user, body, created) 
        self.body = "Originally {event} by [{user}]({url}/{user}) on {created} \r\n\r\n {body}".format(event=event, user=user, created=created, body=body, url=target_url)

    def __str__(self):
        return self.body 

def list_org_repos(url, org, token):
    def check_follow_pagination(res, repos_list):
        try:
            last = res.headers["Link"].split(",")[1].split(">;")[0].split("page=")[1]
            url = res.headers["Link"].split(",")[0].split(">;")[0][1:-1]
            fetch_repos = lambda url: requests.request("GET", headers=source_headers, url=url)
            for i in range(2, int(last) + 1):
                for item in fetch_repos(url + str(i)).json():
                    repo = Repo(item["name"], item["private"], item["description"])
                    repos_list.append(repo)
        except Exception:
            pass
    try:
        headers = {"Authorization" : "token {}".format(token)}
        response = requests.request("GET", url="{}/orgs/{}/repos".format(url, org), headers=headers)
        if response.status_code == 200:
            repos_list = []
        for item in response.json():
            repo = Repo(item["name"], item["private"], item["description"])
            repos_list.append(repo)
        check_follow_pagination(response, repos_list)
        return repos_list
    except Exception:
        logger.warning("{} organization does not exist in target GitHub: {}".format(org, fetch_url_from_api(url)))

def fetch_source_teams():
    teams = []
    try:
        response = requests.request("GET", headers=source_headers, url="{}/orgs/{}/teams".format(source_api_url, source_org)) 
        for team in response.json():
            fetch_data = lambda url: requests.request("GET", headers=source_headers, url=url).json()
            repos = []
            for repo in fetch_data(team["repositories_url"]):
                repos.append(repo["full_name"].replace(source_org, target_org))
            maintainers = []
            members = []
            ldap_dn = ""
            try:
                if team["ldap_dn"]:
                    ldap_dn = team["ldap_dn"]
            except Exception:
                for mem in fetch_data(team["members_url"].split("{")[0]):
                    if fetch_data(team["members_url"].split("/members")[0]+ "/memberships/" + mem["login"])["role"] == "maintainer":
                        maintainers.append(mem["login"])
                    members.append(mem["login"])
            team = Team(team["name"], team["description"], team["privacy"], repos, members, maintainers, ldap_dn)
            teams.append(team)
    except Exception:
        logger.error("Exception occurred: ", exc_info=True)
    return teams

def create_teams(teams):
    for team in teams:
        logger.info("Creating team {} in target organization {}".format(str(team), target_org))
        try:
            response = requests.request("POST", url="{}/orgs/{}/teams".format(target_api_url, target_org), data=json.dumps(vars(team)), headers=target_headers)
            if response.status_code == 201:
                logger.info("Team {} created successfully".format(str(team)))
            else:
                logger.error("Team {} creation failed".format(str(team)))
        except Exception:
            logger.error("Team {} creation failed : ".format(str(team)), exc_info=True)

def fetch_org_members():
    members = []
    admins = []
    logger.info("Fetching member list from source organization {}".format(source_org))
    def check_follow_pagination(mems, members):
        try:
            last = mems.headers["Link"].split(",")[1].split(">;")[0].split("page=")[1]
            url = mems.headers["Link"].split(",")[0].split(">;")[0][1:-1]
            for i in range(2, int(last) + 1):
                for mem in fetch_members(url + str(i)).json():
                    members.append(mem["login"])
        except Exception:
            pass
    try:
        
        fetch_members = lambda url: requests.request("GET", headers=source_headers, url=url)
        initial_fetch_members = fetch_members("{}/orgs/{}/members?role=members".format(source_api_url, source_org))
        for mem in initial_fetch_members.json():
            members.append(mem["login"])
        check_follow_pagination(initial_fetch_members, members)
        initial_fetch_admins = fetch_members("{}/orgs/{}/members?role=admin".format(source_api_url, source_org))
        for mem in initial_fetch_admins.json():
            admins.append(mem["login"])
        check_follow_pagination(initial_fetch_admins, admins)
        logger.info("Member list fetched successfully!!!")
    except Exception:
        logger.error("Failed to fetch member list from source organization {}".format(source_org))
    #admins = ['smallidi']
    return (members, admins)

def fetch_pull_requests(repo):
    prs = []
    logger.info("Fetching pull request details from source repo: {}".format(repo))
    try:
        response = requests.request("GET", headers=source_headers, url="{}/repos/{}/{}/pulls".format(source_api_url, source_org, repo)).json()
        
        for pr in response:
            #print(pr)
            reviewers = [item["login"] for item in pr["requested_reviewers"]]
            assignees = [item["login"] for item in pr["assignees"]]
            review_comments = [ReviewComment(item["user"]["login"], item["body"], item["updated_at"], item["original_commit_id"], item["original_position"], item["path"]) for item in requests.get(headers=source_headers, url=pr["_links"]["review_comments"]["href"]).json()]
            comments = [Comment(item["user"]["login"], item["body"], item["updated_at"]) for item in requests.get(headers=source_headers, url=pr["_links"]["comments"]["href"]).json()]
            reviews = [Review(item["user"]["login"], item["body"], item["submitted_at"], item["state"]) for item in requests.get(headers=source_headers, url="{}/reviews".format(pr["_links"]["review_comments"]["href"].split("/comments")[0])).json() if item["body"] != ""]
            new_pr = pull_request(pr["number"], pr["user"]["login"],pr["title"], pr["body"], pr["created_at"],pr["head"]["ref"], pr["base"]["ref"], assignees, reviewers, reviews, review_comments, comments)
            prs.append(new_pr)
        logger.info("Pull requests fetched successfully from source GitHub")
    except Exception:
        logger.error("Failed to fetch pull requests from source repo: {}".format(repo), exc_info=True)
    return prs

def create_pull_requests(repo, prs):
    #create_base_branches()
    def check_status(res):
        if res.status_code != 201:
            raise Exception
    for pr in prs:
        logger.info("Migrating pull request {} to target github".format(pr.number))
        try:
            data={"title" : pr.title, "body" : pr.body, "head": pr.head, "base": pr.base}
            response = requests.request("POST", headers=target_headers, url="{}/repos/{}/{}/pulls".format(target_api_url, target_org, repo), json=data)
            check_status(response)
            pr_number = response.json()["number"]
            # Adding reviewers
            logger.info("Adding reviewers to new pull request {}".format(pr_number))
            res = requests.request("POST", headers=target_headers, json={"reviewers": pr.reviewers}, url="{}/repos/{}/{}/pulls/{}/requested_reviewers".format(target_api_url, target_org, repo, pr_number))
            check_status(res)
            logger.info("Reviewers added to new pull request {} successfully".format(pr_number))
            # Adding assignees
            logger.info("Adding assignees to new pull request {}".format(pr_number))
            r = requests.request("POST", headers=target_headers, json={"assignees": pr.assignees}, url="{}/repos/{}/{}/issues/{}/assignees".format(target_api_url, target_org, repo, pr_number))
            check_status(r)
            logger.info("Assignees added to new pull request {} successfully".format(pr_number))
            all_comments = pr.comments + pr.reviews + pr.review_comments
            all_comments.sort(key= lambda x: x.created)
            add_comment = lambda comment: requests.post(headers=target_headers, data=json.dumps(vars(comment)),url="{}/repos/{}/{}/issues/{}/comments".format(target_api_url, target_org, repo, pr_number))
            add_review_comment = lambda review_comment: requests.post(headers=target_headers, data=json.dumps(vars(review_comment)),url="{}/repos/{}/{}/pulls/{}/comments".format(target_api_url, target_org, repo, pr_number))
            # add_review = lambda review: requests.post(headers=target_headers, data=json.dumps(vars(review)),url="{}/repos/{}/{}/pulls/{}/reviews".format(target_api_url, target_org, repo, pr_number))
            # Adding reviews/comments
            logger.info("Adding reviews/comments to new pull request {}".format(pr_number))
            for comment in all_comments:
                if comment.__class__.__name__ == "Comment":
                    com_res = add_comment(comment)
                elif comment.__class__.__name__ == "ReviewComment":
                    com_res = add_review_comment(comment)
                elif comment.__class__.__name__ == "Review":
                    com_res = add_comment(comment)
                
                check_status(com_res)
            logger.info("Reviews/Comments added to new pull request {} successfully".format(pr_number))
            logger.info("Pull request {} migrated successfully to target GitHub".format(pr.number))
        except Exception:
            logger.error("Failed to migrate pull request {} to target github".format(pr.number))


def add_members_to_org(members, admins):
    add_member = lambda mem, role : requests.request('PUT', headers=target_headers, json={"role": role}, url="{}/orgs/{}/memberships/{}".format(target_api_url, target_org, mem))
    for mem in admins:
        role = "admin"
        res = add_member(mem, role)
        if res.status_code in [200, 422]:
            logger.info("Admin {} added/invited to organization {}".format(mem, target_org))
        else:
            logger.error("Failed to add {} as admin in target organization {}".format(mem, target_org))
    for mem in members:
        role = "member"
        res = add_member(mem, role)
        if res.status_code in [200, 422]:
            logger.info("Member {} added/invited to organization {}".format(mem, target_org))
        else:
            logger.error("Failed to add {} as member in target organization {}".format(mem, target_org))


def create_organization(url, name, user, token):
    data_dict = {}
    data_dict['login'] = name
    data_dict['admin'] = user
    data = json.dumps(data_dict)
    status = True
    try:
        headers = {"Authorization" : "token {}".format(token)}
        response = requests.request("POST", data=data, headers=headers, url="{}/admin/organizations".format(url))
        status = False if response.status_code != 200 else True
    except Exception:
        logger.error("Exception occurred: ", exc_info=True)
        status = False
    return status

def create_repo(url, org, repo, token):
    status = True
    try:
        headers = {"Authorization" : "token {}".format(token)}
        response = requests.request("POST", headers=headers, data=json.dumps(vars(repo)), url="{}/orgs/{}/repos".format(url, org))
        if response.status_code == 422:
            status = True if "already exists" in response.json()["errors"][0]["message"] else False
            logger.info("Repo {} already exists in target GitHub's organization {}".format(repo.name, org))
        else:
            status = True if response.status_code in [200,201] else False
            logger.info("Created repo {} successfully in target GitHub's organization {}".format(repo.name, org))
    except Exception:
        status = False
    return status

def sync_single_repo(repo):
    pluck_http_out_of_url = lambda url: url if "http" not in url else url.split("://")[1]
    create_git_url = lambda user, token, url, org, name : "https://{}:{}@{}/{}/{}.git".format(user, token, pluck_http_out_of_url(url), org, name)
    source = create_git_url(user, source_token, source_url, source_org, repo.name)
    target = create_git_url(user, target_token, target_url, target_org, repo.name)
    logger.info("Creating repository {} in target GitHub {}".format(repo.name, fetch_url_from_api(target_api_url)))
    if create_repo(target_api_url, target_org, repo, target_token):
        cwd = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                logger.info("Created temporary directory for cloning: {}".format(temp_dir))
                logger.info("Cloning source repo {} to temporary directory".format(repo.name))
                subprocess.call(["git", "clone", "--mirror", source, temp_dir])
                logger.info("Cloning completed successfully!!!")
                os.chdir(temp_dir)
                logger.info("Modifying origin url to {}/{}/{}.git".format(target_url, target_org, repo.name))
                subprocess.call(["git", "remote", "rm", "origin"])
                subprocess.call(["git", "remote", "add", "origin", target])
                logger.info("origin modified successfully!!!")
                if os.path.exists("packed-refs"):
                    logger.info("Modifying hidden refs to hidden branches to create pull requests")
                    subprocess.call(["sed", "-i.bak", "s/pull/pr/g", "packed-refs"])
                    logger.info("Modified to hidden branches successfully")
                    logger.info("Pushing code, branches and tags to target GitHub {}".format(target_url))
                    subprocess.call(["git", "push", "--mirror"])
                    logger.info("Push activity completed successfully!!!")
                    #source_prs = fetch_pull_requests(repo.name)
                else:
                    logger.info("Empty repository found, ignoring...")

                    #create_pull_requests(repo.name, source_prs)
                logger.info("Repo {} mirrored successfully in target GitHub {}".format(repo.name, target_org))
        except Exception:
            logger.error("Exception occurred: ", exc_info=True)
        finally:
            os.chdir(cwd)
    else:
        logger.error("Repo {} creation failed in target GitHub {}".format(repo.name, fetch_url_from_api(target_api_url)))

def create_repo_obj_from_name(repo):
    repo_obj = None
    try:
        resp = requests.get(url="{}/repos/{}/{}".format(source_api_url, source_org, repo), headers=source_headers)
        if resp.status_code == 200:
            res = resp.json()
            repo_obj = Repo(res['name'], res['private'], res['description'])
    except Exception:
        pass
    return repo_obj

def sync_repos(repos_list):
    logger.info("Repos to be synced: {}".format(",".join(str(repo) for repo in repos_list)))
    
    for repo in repos_list:
        logger.info("Starting sync for repo: {}".format(repo.name))
        
        sync_single_repo(repo)    

if __name__ == '__main__':
    logger.info("Source git: {} Source Organization: {}".format(fetch_url_from_api(source_api_url), source_org))
    logger.info("Target git: {} Target Organization: {}".format(fetch_url_from_api(target_api_url), target_org))

    repos_list = args.repos
    if repos_list is None:
        repos_list = list_org_repos(source_api_url, source_org, source_token)
    else:
        repos = []
        for repo in repos_list:
            repos.append(create_repo_obj_from_name(repo))
        repos_list = [ repo for repo in repos if repo != None]

    #print(repos_list)
    # Verify if target organization is available
    target_repos_list = list_org_repos(target_api_url, target_org, target_token)
    if target_repos_list is None:
        if args.site_admin:
            logger.info("Creating organization {} in target GitHub: {}".format(target_org, fetch_url_from_api(target_api_url)))
            if create_organization(target_api_url, target_org, args.site_admin, target_token):
                logger.info("Organization {} created successfully in target GitHub {}".format(target_org, fetch_url_from_api(target_api_url)))
            else:
                logger.error("Failed to create organization {} in target GitHub {}".format(target_org, fetch_url_from_api(target_api_url)))
                sys.exit(1)
        else:
            sys.exit(1)
    else:
        logger.info("Organization {} exists in target GitHub: {}".format(target_org, fetch_url_from_api(target_api_url)))
    
    (members,admins) = fetch_org_members()
    add_members_to_org(members,admins)
    teams = fetch_source_teams()
    create_teams(teams) 
    sync_repos(repos_list)  
    logger.info("Organization {} migrated from Source {} to Target {} successfully!".format(source_org, fetch_url_from_api(source_api_url), fetch_url_from_api(target_api_url))) 

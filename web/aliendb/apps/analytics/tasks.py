import datetime
import os
import string
from celery import Celery
import praw
import prawcore
from textblob import TextBlob
from django.core.cache import cache
from .helpers import *
from .models import *

app = Celery('tasks')
app.config_from_object('django.conf:settings')

reddit = praw.Reddit(client_id=os.environ['PRAW_CLIENT_ID'],
                     client_secret=os.environ['PRAW_CLIENT_SECRET'],
                     username=os.environ['PRAW_REDDIT_USERNAME'],
                     password=os.environ['PRAW_REDDIT_PASSWORD'],
                     user_agent=os.environ['PRAW_USER_AGENT'])


def create_submission_obj(submission, rank) -> Submission:
    """Creates a models.Submission object from a Praw submission object.

    Args:
        submission: the source Praw submission object;
        rank: the current rank (1-100) of the submission

    Returns:
        Submission: the created models.Submission object
    """
    created_at = datetime.datetime.utcfromtimestamp(submission.created_utc)
    created_at = created_at.replace(tzinfo=datetime.timezone.utc)

    # get subreddit
    try:
        subreddit = Subreddit.objects.get(name=submission.subreddit)
    except Subreddit.DoesNotExist:
        # subreddit obj doesn't exist; create it
        subreddit = Subreddit.objects.create(name=submission.subreddit)
        subreddit.title = submission.subreddit.title
        if hasattr(submission.subreddit, 'public_description'):
            subreddit.description = submission.subreddit.public_description
        else:
            subreddit.description = ''
        subreddit.save()

    author = ''
    if hasattr(submission, 'author'):
        if submission.author is not None:
            author = submission.author.name

    # perform sentiment analysis on submission title
    blob = TextBlob(submission.title)
    polarity = blob.polarity
    subjectivity = blob.subjectivity

    # create Submission object
    submission_obj = Submission(id=submission.id,
                                subreddit=subreddit,
                                title=submission.title,
                                author=author,
                                rank=rank,
                                rank_previous=rank,
                                rank_peak=rank,
                                score=submission.score,
                                num_comments=submission.num_comments,
                                polarity=polarity,
                                subjectivity=subjectivity,
                                domain=submission.domain,
                                link_flair_text=submission.link_flair_text or '',
                                upvote_ratio=submission.upvote_ratio,
                                stickied=submission.stickied,
                                over_18=submission.over_18,
                                spoiler=submission.spoiler,
                                locked=submission.locked,
                                gilded_silver=submission.gildings['gid_1'],
                                gilded_gold=submission.gildings['gid_2'],
                                gilded_platinum=submission.gildings['gid_3'],
                                created_at=created_at)
    submission_obj.save()

    # create Comment objects
    submission.comments.replace_more(limit=0)
    comments = submission.comments.list()

    if submission.num_comments > 500:
        # get the submission again, sorted by oldest comments
        submission.comment_sort = 'old'
        submission = reddit.submission(id=submission.id)
        submission.comments.replace_more(limit=0)
        # append new flattened comments to comments array
        comments += submission.comments.list()

    for comment in comments:
        create_comment_obj(comment, submission_obj)

    return submission_obj


def update_submission_obj(submission, rank) -> Submission:
    """Updates an existing models.Submission object from a Praw submission object.

    Args:
        submission: the source Praw submission object;
        rank: the current rank (1-100) of the submission

    Returns:
        Submission: the updated models.Submission object
    """
    submission_obj = Submission.objects.get(id=submission.id)

    # determine rank on /r/all
    submission_obj.rank_previous = submission_obj.rank
    submission_obj.rank = rank
    if rank < submission_obj.rank_peak:
        submission_obj.rank_peak = rank

    # update Submission obj
    submission.comments.replace_more(limit=0)
    comments = submission.comments.list()

    # update submission details
    submission_obj.score = submission.score
    submission_obj.num_comments = submission.num_comments
    if submission.link_flair_text is not None:
        submission_obj.link_flair_text = submission.link_flair_text
    submission_obj.upvote_ratio = submission.upvote_ratio
    submission_obj.stickied = submission.stickied
    submission_obj.over_18 = submission.over_18
    submission_obj.spoiler = submission.spoiler
    submission_obj.locked = submission.locked
    submission_obj.gilded_silver = submission.gildings['gid_1']
    submission_obj.gilded_gold = submission.gildings['gid_2']
    submission_obj.gilded_platinum = submission.gildings['gid_3']

    # create new Comment objects if necessary
    if submission.num_comments > 500:
        # get the submission again, sorted by oldest comments
        submission = reddit.submission(id=submission.id)
        submission.comment_sort = 'old'
        submission.comments.replace_more(limit=0)
        # append new flattened comments to comments array
        comments += submission.comments.list()

    for comment in comments:
        create_comment_obj(comment, submission_obj)

    return submission_obj


def create_comment_obj(comment, submission_obj):
    """Creates a models.Comment object from a Praw comment object.

    Args:
        comment: the source Praw comment object;
        submission_obj: the models.Submission object parent to the comment
    """
    # check if comment already exists in db
    if not Comment.objects.filter(id=comment.id).exists():
        # check if comment has been deleted
        if not hasattr(comment, 'body'):
            return

        # determine comment distinguised properties
        if comment.author is not None:
            is_op = comment.author.name == submission_obj.author
            if comment.distinguished:
                is_mod = 'moderator' in comment.distinguished
                is_admin = 'admin' in comment.distinguished
                is_special = 'special' in comment.distinguished
            else:
                is_mod = False
                is_admin = False
                is_special = False
        else:
            # author field is None, which means the user deleted their account
            is_op = None
            is_mod = None
            is_admin = None
            is_special = None

        # perform sentiment analysis on comment
        blob = TextBlob(comment.body)
        polarity = blob.polarity
        subjectivity = blob.subjectivity

        num_characters = sum(c in string.ascii_letters for c in comment.body)

        created_at = datetime.datetime.utcfromtimestamp(comment.created_utc)
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)

        comment_obj = Comment(id=comment.id,
                              submission=submission_obj,
                              score=comment.score,
                              is_root=comment.is_root,
                              is_op=is_op,
                              is_mod=is_mod,
                              is_admin=is_admin,
                              is_special=is_special,
                              gilded_silver=comment.gildings['gid_1'],
                              gilded_gold=comment.gildings['gid_2'],
                              gilded_platinum=comment.gildings['gid_3'],
                              characters=num_characters,
                              words=len(blob.words),
                              sentences=len(blob.sentences),
                              polarity=polarity,
                              subjectivity=subjectivity,
                              created_at=created_at)
        comment_obj.save()

        # update subreddit stats
        subreddit = comment_obj.submission.subreddit
        subreddit.average_comments_polarity = update_average(
            subreddit.average_comments_polarity,
            polarity,
            subreddit.tracked_comments)
        subreddit.average_comments_subjectivity = update_average(
            subreddit.average_comments_subjectivity,
            subjectivity,
            subreddit.tracked_comments)
        subreddit.tracked_comments = subreddit.tracked_comments + 1
        subreddit.save()

    else:
        # comment already exists in db
        comment_obj = Comment.objects.get(id=comment.id)
        comment_obj.score = comment.score
        comment_obj.gilded_silver = comment.gildings['gid_1']
        comment_obj.gilded_gold = comment.gildings['gid_2']
        comment_obj.gilded_platinum = comment.gildings['gid_3']
        comment_obj.save()


def update_subreddit_obj(submission_obj) -> Subreddit:
    """Updates an existing models.Subreddit object with a submission's stats.

    Args:
        submission_obj: the models.Submission object to get stats from

    Returns:
        Subreddit: the updated models.Subreddit object
    """
    subreddit = submission_obj.subreddit
    comments = Comment.objects.filter(submission=submission_obj)

    current_gilded_silver = sum(c.gilded_silver for c in comments)
    current_gilded_gold = sum(c.gilded_gold for c in comments)
    current_gilded_platinum = sum(c.gilded_platinum for c in comments)
    current_is_op = [c.is_op for c in comments].count(True)
    current_is_mod = [c.is_mod for c in comments].count(True)
    current_is_admin = [c.is_admin for c in comments].count(True)
    current_is_special = [c.is_special for c in comments].count(True)

    subreddit.average_submission_polarity = update_average(
        subreddit.average_submission_polarity,
        submission_obj.polarity,
        subreddit.tracked_submissions)
    subreddit.average_submission_subjectivity = update_average(
        subreddit.average_submission_subjectivity,
        submission_obj.subjectivity,
        subreddit.tracked_submissions)
    subreddit.average_upvote_ratio = update_average(
        subreddit.average_upvote_ratio,
        submission_obj.upvote_ratio,
        subreddit.tracked_submissions)
    subreddit.average_gilded_silver = update_average(
        subreddit.average_gilded_silver,
        current_gilded_silver,
        subreddit.tracked_submissions)
    subreddit.average_gilded_gold = update_average(
        subreddit.average_gilded_gold,
        current_gilded_gold,
        subreddit.tracked_submissions)
    subreddit.average_gilded_platinum = update_average(
        subreddit.average_gilded_platinum,
        current_gilded_platinum,
        subreddit.tracked_submissions)
    subreddit.average_is_op = update_average(
        subreddit.average_is_op,
        current_is_op,
        subreddit.tracked_submissions)
    subreddit.average_is_mod = update_average(
        subreddit.average_is_mod,
        current_is_mod,
        subreddit.tracked_submissions)
    subreddit.average_is_admin = update_average(
        subreddit.average_is_admin,
        current_is_admin,
        subreddit.tracked_submissions)
    subreddit.average_is_special = update_average(
        subreddit.average_is_special,
        current_is_special,
        subreddit.tracked_submissions)

    subreddit.score = subreddit.score + submission_obj.score
    subreddit.num_comments = subreddit.num_comments + submission_obj.num_comments
    subreddit.tracked_submissions = subreddit.tracked_submissions + 1
    subreddit.save()

    return subreddit


def create_submission_tracker_objs(submission_obj, submission):
    """Creates tracker objects for a given submission.

    Creates the following objects in the database for this submission:
        SubmissionScore, SubmissionNumComments, SubmissionUpvoteRatio

    Args:
        submission_obj: the source models.Submission object;
        submission: the source Praw submission object
    """
    submission_score = SubmissionScore(
        submission=submission_obj,
        score=submission.score)
    submission_num_comments = SubmissionNumComments(
        submission=submission_obj,
        num_comments=submission.num_comments)
    submission_upvote_ratio = SubmissionUpvoteRatio(
        submission=submission_obj,
        upvote_ratio=submission.upvote_ratio)

    submission_score.save()
    submission_num_comments.save()
    submission_upvote_ratio.save()


def create_cumulative_tracker_objs(submission_obj):
    """Creates cumulative tracker objects from a given submission.

    This function should only be run once for each submission -- presumably
    at the moment the submission leaves the top 100.

    Creates the following objects in the database:
        SubmissionScore, SubmissionNumComments, SubmissionUpvoteRatio

    Args:
        submission_obj: models.Submission object to get stats from
    """
    try:
        latest_score = TotalScore.objects.latest('timestamp').score
        latest_num_comments = TotalNumComments.objects.latest(
            'timestamp').num_comments
    except TotalScore.DoesNotExist:
        latest_score = 0
        latest_num_comments = 0

    total_score = TotalScore(
        score=latest_score + submission_obj.score)
    total_num_comments = TotalNumComments(
        num_comments=latest_num_comments + submission_obj.num_comments)

    total_score.save()
    total_num_comments.save()


def create_subreddit_tracker_objs(subreddit):
    """Creates tracker objects for a given subreddit.

    Creates the following objects in the database for this subreddit:
        SubredditScore, SubredditNumComments

    Args:
        subreddit: the source models.Subreddit object
    """
    subreddit_score = SubredditScore(
        subreddit=subreddit,
        score=subreddit.score)
    subreddit_num_comments = SubredditNumComments(
        subreddit=subreddit,
        num_comments=subreddit.num_comments)

    subreddit_score.save()
    subreddit_num_comments.save()


@app.task
def get_top_submissions():
    """Retrieves the top 100 posts on /r/all and creates appropriate DB objs.

    This function is the main driver for collecting stats. It retrieves a list
    of the top submissions and creates corresponding objects in the database.

    Subreddit and cumulative tracker objects are created when submissions leave
    the top 100, as a way to ensure they are only tallied once.
    """
    subreddit = reddit.subreddit('all')

    # get current top 100 submissions
    try:
        submissions = [submission for submission in subreddit.hot(limit=100)]
    except prawcore.exceptions.RequestException:
        # reddit api is likely unavailable
        return

    # create/update db submission objects
    rank = 0
    submission_objs = []
    frontpage_score = 0
    frontpage_num_comments = 0
    for submission in submissions:
        rank += 1

        # track running variables
        frontpage_score += submission.score
        frontpage_num_comments += submission.num_comments

        try:
            # check if this submission already exists in the db
            if Submission.objects.filter(id=submission.id):
                submission_obj = update_submission_obj(submission, rank)
            else:
                submission_obj = create_submission_obj(submission, rank)
            submission_objs.append(submission_obj)
            create_submission_tracker_objs(submission_obj, submission)
        except prawcore.exceptions.RequestException:
            # reddit api is likely unavailable
            continue

    # reset rank for submissions no longer in top 100
    submission_ids = [submission.id for submission in submissions]
    modified_subreddits = []
    for submission_obj in Submission.objects.filter(rank__gt=0):
        if submission_obj.id not in submission_ids:
            create_cumulative_tracker_objs(submission_obj)
            subreddit = update_subreddit_obj(submission_obj)

            if subreddit not in modified_subreddits:
                modified_subreddits.append(subreddit)

            submission_obj.rank = -1
            submission_obj.save()

    for subreddit in modified_subreddits:
        create_subreddit_tracker_objs(subreddit)

    # create new frontpage tracker objects
    average_score = AverageScore(score=frontpage_score/100)
    average_score.save()
    average_num_comments = AverageNumComments(
        num_comments=frontpage_num_comments/100)
    average_num_comments.save()

    # save all submission db objects
    for submission_obj in submission_objs:
        submission_obj.save()

    # delete cached page responses
    cache.delete("home_response")
    cache.delete("subreddits_response")

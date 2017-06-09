from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.db.models import Q
from .models import *
from . import reports
from redis import Redis

from datetime import datetime, timedelta
import time

redis = Redis(host='redis', port=6379)


def home(request):
    submissions = Submission.objects.filter(rank__gt=0).order_by('rank')

    # calculate rank deltas
    for submission in submissions:
        rank_delta = submission.rank_previous - submission.rank
        if rank_delta > 0:
            shape = '▲'
            color = 'green'
        elif rank_delta < 0:
            shape = '▼'
            color = 'red'
        else:
            shape = '▬'
            color = 'orange'
        submission.delta_color = color
        submission.delta_string = "%s%d" % (shape, rank_delta)

    return render(request, 'home.html', {
        'submissions': submissions,
    })

def about(request):
    return render(request, 'about.html')

def api(request):
    name = request.GET.get('name', '')

    if name == 'bulk':
        data = reports.bulk(request)
    elif name == 'activity':
        data = reports.activity(request)
    elif name == 'upvote_ratio':
        data = reports.upvote_ratio(request)
    elif name == 'special_users':
        data = reports.special_users(request)
    elif name == 'gilded':
        data = reports.gilded(request)
    elif name == 'polarity':
        data = reports.polarity(request)
    elif name == 'subjectivity':
        data = reports.subjectivity(request)

    return JsonResponse(data)

def submission(request, id):
    submission = Submission.objects.get(id=id)
    comments = Comment.objects.filter(submission=submission)
    subreddit_submissions = Submission.objects.filter(subreddit=submission.subreddit)
    subreddit_comments = [c for queryset in [Comment.objects.filter(submission_id=s.id) for s in subreddit_submissions] for c in queryset]
    submission_scores = SubmissionScore.objects.filter(submission=submission).order_by('timestamp')
    submission_num_comments = SubmissionNumComments.objects.filter(submission=submission).order_by('timestamp')

    # lifetime and rise time
    lifetime_delta = submission_scores[len(submission_scores) - 1].timestamp - submission_scores[0].timestamp
    lifetime = time.strftime('%H:%M:%S', time.gmtime(lifetime_delta.seconds))

    rise_time_delta = submission_scores[0].timestamp - submission.created_at
    rise_time = time.strftime('%H:%M:%S', time.gmtime(rise_time_delta.seconds))

    return render(request, 'submission.html', {
        'submission': submission,
        'lifetime': lifetime,
        'rise_time': rise_time,
    })

def subreddit(request, subreddit):
    submissions = Submission.objects.filter(subreddit__iexact=subreddit).order_by('-karma_peak')

    total_karma = sum(submission.karma_peak for submission in submissions)
    total_comments = sum(submission.comments_peak for submission in submissions)

    return render(request, 'subreddit.html', {
        'subreddit': subreddit,
        'total_karma': total_karma,
        'total_comments': total_comments,
        'submissions': submissions,
    })

def search(request):
    query = request.GET.get('q', '')
    order_by = request.GET.get('order_by', '')
    time = request.GET.get('time', '')
    subreddits = request.GET.get('subreddits', '')

    # if no query was given
    if not query:
        return render(request, 'search.html')
    else:
        # strip leading and trailing whitespace
        query = query.strip()

    # determine if query is a link to a submission
    if "//reddit.com" in query or "//www.reddit.com" in query:
        submission_id = query.split('/comments/')[1].split('/')[0]
        return redirect('/submission/%s' % submission_id)
    elif "//redd.it" in query:
        submission_id = query.split('//redd.it/')[1]
        return redirect('/submission/%s' % submission_id)
    else:
        if len(query) < 300:
            # get all matching submissions
            submissions = Submission.objects.filter(title__icontains=query)

            # order submissions
            # order_by == 'relevance' is just the default order
            if order_by == 'karma':
                submissions = submissions.order_by('-karma_peak')
            elif order_by == 'comments':
                submissions = submissions.order_by('-comments_peak')

            # remove submissions not in time frame
            if time and time != 'all':
                current_time = datetime.utcnow()
                if time == 'today':
                    prev_time = current_time - timedelta(days=1)
                elif time == 'week':
                    prev_time = current_time - timedelta(weeks=1)
                elif time == 'month':
                    prev_time = current_time - timedelta(days=30)
                elif time == 'year':
                    prev_time = current_time - timedelta(days=365)
                else:
                    prev_time = current_time
                submissions = submissions.filter(created_at__gte=prev_time)

            # remove submissions not in requested subreddits
            if subreddits is not None and subreddits is not '':
                subreddits_arr = subreddits.split(',')
                # create query which ORs iexact matches of subreddit names
                query_objs = Q()
                for s in subreddits_arr:
                    query_objs.add(Q(subreddit__iexact=s), Q.OR)

                submissions = submissions.filter(query_objs)

        return render(request, 'search.html', {
            'submissions': submissions,
            'query': query,
            'order_by': order_by,
            'time': time,
            'subreddits': subreddits,
        })

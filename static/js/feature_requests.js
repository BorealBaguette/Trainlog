(() => {
  const config = window.featureRequestsConfig || {};
  const i18n = window.featureRequestsI18n || {};

  const text = {
    upvoters: i18n.upvoters || 'Upvoters',
    downvoters: i18n.downvoters || 'Downvoters',
    noVotes: i18n.noVotes || 'No votes yet',
    errorLoading: i18n.errorLoading || 'Error loading voters',
    voteError: i18n.voteError || 'Vote failed. Please try again.'
  };

  const votersUrlTemplate =
    config.votersUrlTemplate || '/feature_requests/__ID__/voters';

  const replaceId = (template, id) => template.replace('__ID__', id);

  window.showVoters = function showVoters(requestId) {
    const modalEl = document.getElementById('votersModal');
    if (!modalEl) return;

    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    fetch(replaceId(votersUrlTemplate, requestId))
      .then(response => response.json())
      .then(data => {
        let content = '';

        if (data.upvoters.length > 0) {
          content += '<h6 class="text-success"><i class="fa-solid fa-chevron-up"></i> ' + text.upvoters + ' (' + data.upvoters.length + ')</h6>';
          content += '<ul class="list-unstyled mb-3">';
          data.upvoters.forEach(voter => {
            content += `<li><small>${voter.username} <span class="text-muted">• ${new Date(voter.created).toLocaleDateString()}</span></small></li>`;
          });
          content += '</ul>';
        }

        if (data.downvoters.length > 0) {
          content += '<h6 class="text-danger"><i class="fa-solid fa-chevron-down"></i> ' + text.downvoters + ' (' + data.downvoters.length + ')</h6>';
          content += '<ul class="list-unstyled">';
          data.downvoters.forEach(voter => {
            content += `<li><small>${voter.username} <span class="text-muted">• ${new Date(voter.created).toLocaleDateString()}</span></small></li>`;
          });
          content += '</ul>';
        }

        if (data.upvoters.length === 0 && data.downvoters.length === 0) {
          content = '<p class="text-muted text-center">' + text.noVotes + '</p>';
        }

        const contentEl = document.getElementById('votersContent');
        if (contentEl) {
          contentEl.innerHTML = content;
        }
      })
      .catch(error => {
        console.error('Error:', error);
        const contentEl = document.getElementById('votersContent');
        if (contentEl) {
          contentEl.innerHTML = '<p class="text-danger">' + text.errorLoading + '</p>';
        }
      });
  };

  function updateVoteUI(payload) {
    const requestId = String(payload.request_id);
    const scoreEl = document.querySelector(`[data-vote-score][data-request-id="${requestId}"]`);
    const upEl = document.querySelector(`[data-vote-up][data-request-id="${requestId}"]`);
    const downEl = document.querySelector(`[data-vote-down][data-request-id="${requestId}"]`);
    const upBtn = document.querySelector(`[data-vote-button][data-request-id="${requestId}"][data-vote-type="upvote"]`);
    const downBtn = document.querySelector(`[data-vote-button][data-request-id="${requestId}"][data-vote-type="downvote"]`);

    if (scoreEl) {
      scoreEl.textContent = payload.score;
      scoreEl.classList.remove('text-success', 'text-danger', 'text-muted');
      if (payload.score > 0) {
        scoreEl.classList.add('text-success');
      } else if (payload.score < 0) {
        scoreEl.classList.add('text-danger');
      } else {
        scoreEl.classList.add('text-muted');
      }
    }

    if (upEl) upEl.textContent = payload.upvotes;
    if (downEl) downEl.textContent = payload.downvotes;

    if (upBtn && downBtn) {
      const userVote = payload.user_vote;
      upBtn.classList.remove('btn-success', 'btn-outline-success');
      downBtn.classList.remove('btn-danger', 'btn-outline-danger');
      upBtn.classList.add(userVote === 1 ? 'btn-success' : 'btn-outline-success');
      downBtn.classList.add(userVote === -1 ? 'btn-danger' : 'btn-outline-danger');
    }
  }

  function setVoteLoading(requestId, isLoading, activeButton = null) {
    const buttons = document.querySelectorAll(`[data-vote-button][data-request-id="${requestId}"]`);
    buttons.forEach(button => {
      if (!button.dataset.voteOriginalHtml) {
        button.dataset.voteOriginalHtml = button.innerHTML;
      }
      if (isLoading) {
        button.disabled = true;
        button.classList.add('disabled');
      } else {
        button.disabled = false;
        button.classList.remove('disabled');
        button.innerHTML = button.dataset.voteOriginalHtml;
      }
    });

    if (isLoading && activeButton) {
      activeButton.dataset.voteLoading = 'true';
      activeButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    } else if (activeButton) {
      activeButton.dataset.voteLoading = 'false';
    }
  }

  function handleVoteClick(event) {
    const button = event.currentTarget;
    if (button.dataset.voteLoading === 'true') {
      return;
    }
    const requestId = button.dataset.requestId;
    const voteType = button.dataset.voteType;
    const voteUrl = button.dataset.voteUrl;
    if (!requestId || !voteType || !voteUrl) {
      alert(text.voteError);
      return;
    }

    setVoteLoading(requestId, true, button);

    const formData = new FormData();
    formData.append('request_id', requestId);
    formData.append('vote_type', voteType);

    fetch(voteUrl, {
      method: 'POST',
      body: formData,
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(async response => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data.error || 'Vote failed');
        }
        return data;
      })
      .then(updateVoteUI)
      .catch(() => {
        alert(text.voteError);
      })
      .finally(() => {
        setVoteLoading(requestId, false, button);
      });
  }

  function initVoteButtons() {
    document.querySelectorAll('[data-vote-button]').forEach(button => {
      button.addEventListener('click', handleVoteClick);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVoteButtons);
  } else {
    initVoteButtons();
  }
})();

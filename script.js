let allConferences = [];
let filteredConferences = [];

document.addEventListener('DOMContentLoaded', () => {
    loadCSVData();
    setupEventListeners();
});

function loadCSVData() {
    Papa.parse('conferences_filtrees.csv', {
        download: true,
        header: true,
        skipEmptyLines: true,
        complete: function(results) {
            allConferences = results.data;
            filteredConferences = [...allConferences];
            renderTable(filteredConferences);
        },
        error: function(err) {
            console.error('Error loading CSV:', err);
            document.getElementById('table-body').innerHTML = `<tr><td colspan="8" style="text-align:center; color: #f87171;">Failed to load CSV data. Make sure "conferences_filtrees.csv" is available.</td></tr>`;
        }
    });
}

function setupEventListeners() {
    document.getElementById('theme-filter').addEventListener('input', applyFilters);
    document.getElementById('rank-filter').addEventListener('change', applyFilters);
    document.getElementById('date-depot-filter').addEventListener('change', applyFilters);
    document.getElementById('date-reponse-filter').addEventListener('change', applyFilters);
    
    document.getElementById('reset-btn').addEventListener('click', () => {
        document.getElementById('theme-filter').value = '';
        document.getElementById('rank-filter').value = '';
        document.getElementById('date-depot-filter').value = '';
        document.getElementById('date-reponse-filter').value = '';
        applyFilters();
    });

    document.getElementById('export-btn').addEventListener('click', exportCSV);
}

function applyFilters() {
    const theme = document.getElementById('theme-filter').value.toLowerCase();
    const rank = document.getElementById('rank-filter').value;
    const dateDepot = document.getElementById('date-depot-filter').value;
    const dateReponse = document.getElementById('date-reponse-filter').value;

    filteredConferences = allConferences.filter(conf => {
        // Theme filter (checks Topics and Short Description)
        const topics = (conf.Topics || '').toLowerCase();
        const desc = (conf['Short Description'] || '').toLowerCase();
        const matchesTheme = theme === '' || topics.includes(theme) || desc.includes(theme);

        // Rank filter
        const matchesRank = rank === '' || conf.Rank === rank;

        // Date Submission filter (date depot)
        let matchesDepot = true;
        if (dateDepot && conf['Submission Deadline']) {
            matchesDepot = conf['Submission Deadline'] >= dateDepot;
        }

        // Date Notification filter (date reponse)
        let matchesReponse = true;
        if (dateReponse && conf['Notification Date']) {
            matchesReponse = conf['Notification Date'] >= dateReponse;
        }

        return matchesTheme && matchesRank && matchesDepot && matchesReponse;
    });

    renderTable(filteredConferences);
}

function getRankBadgeClass(rank) {
    if (rank === 'A*') return 'rank-A-star';
    if (rank === 'A') return 'rank-A';
    if (rank === 'B') return 'rank-B';
    if (rank === 'C') return 'rank-C';
    return '';
}

function renderTable(data) {
    const tbody = document.getElementById('table-body');
    tbody.innerHTML = '';

    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 2rem; color: #94a3b8;">No conferences found matching your criteria.</td></tr>`;
        return;
    }

    data.forEach(conf => {
        const tr = document.createElement('tr');
        
        // Rank Badge
        const rankBadge = conf.Rank && conf.Rank !== 'N/A' ? `<span class="badge ${getRankBadgeClass(conf.Rank)}">${conf.Rank}</span>` : '<span class="badge" style="background: rgba(255,255,255,0.1)">N/A</span>';
        
        // URL
        let urlHtml = '<span style="color: #64748b;">N/A</span>';
        if (conf.URL && conf.URL !== 'N/A') {
            urlHtml = `<a href="${conf.URL}" target="_blank" class="link-btn">
                Visit
                <svg style="width: 14px; height: 14px;" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
            </a>`;
        }

        // Topics tooltip
        const topicsText = conf.Topics ? conf.Topics : 'N/A';
        const formattedTopics = topicsText.replace(/"/g, '&quot;');

        tr.innerHTML = `
            <td><strong>${conf.Acronym || 'N/A'}</strong></td>
            <td>${conf.Name || 'N/A'}</td>
            <td>${rankBadge}</td>
            <td>${conf['Submission Deadline'] || 'N/A'}</td>
            <td>${conf['Notification Date'] || 'N/A'}</td>
            <td class="topics-cell" title="${formattedTopics}">${topicsText}</td>
            <td>
                <span style="font-size: 0.85rem; color: ${conf.Status && conf.Status.includes('Success') ? '#34d399' : '#f87171'};">
                    ${conf.Status || 'N/A'}
                </span>
            </td>
            <td>${urlHtml}</td>
        `;
        tbody.appendChild(tr);
    });
}

function exportCSV() {
    if (filteredConferences.length === 0) {
        alert('No data to export!');
        return;
    }

    const csv = Papa.unparse(filteredConferences);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    
    link.setAttribute('href', url);
    link.setAttribute('download', 'filtered_conferences.csv');
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

let allConferences = [];
let filteredConferences = [];
let themeGroups = {}; // code -> { code, name, subareas: [] }
let acronymToForMap = {}; // acronym.toLowerCase() -> [for1, for2, for3]
let currentSortColumn = null;
let currentSortDirection = 'asc'; // 'asc' or 'desc'

document.addEventListener('DOMContentLoaded', () => {
    loadAllData();
});

// Helper to parse CSV using Promises
function parseCSV(url, hasHeader = true) {
    return new Promise((resolve, reject) => {
        Papa.parse(url, {
            download: true,
            header: hasHeader,
            skipEmptyLines: true,
            complete: (results) => resolve(results.data),
            error: (err) => reject(err)
        });
    });
}

async function loadAllData() {
    try {
        const [confData, forData, coreData] = await Promise.all([
            parseCSV('conferences_filtrees.csv', true),
            parseCSV('FoRcode_details.csv', true),
            parseCSV('CORE_all26.csv', false) // CORE has no headers in this file
        ]);

        allConferences = confData;
        
        // Build Acronym -> FoR Codes map
        coreData.forEach(row => {
            const acronym = row[2];
            if (acronym) {
                const codes = [];
                if (row[6]) codes.push(String(row[6]).trim());
                if (row[7]) codes.push(String(row[7]).trim());
                if (row[8]) codes.push(String(row[8]).trim());
                acronymToForMap[acronym.toLowerCase()] = codes.filter(c => c && c !== 'None' && c !== 'nan' && c !== 'NaN' && c !== 'N/A');
            }
        });

        // Build Theme Hierarchy
        forData.forEach(row => {
            const code = String(row.code).trim();
            const name = String(row.name).trim();
            const parent = String(row.parent_code || '').trim();
            const level = String(row.level).trim();

            if (level === 'Group') {
                if (!themeGroups[code]) {
                    themeGroups[code] = { code, name, subareas: [] };
                } else {
                    themeGroups[code].name = name; // Update name if placeholder existed
                }
            } else if (level === 'Subarea') {
                const subarea = { code, name, parent };
                if (!themeGroups[parent]) {
                    themeGroups[parent] = { code: parent, name: 'Other Group', subareas: [] };
                }
                themeGroups[parent].subareas.push(subarea);
            }
        });

        // Render Themes tree DOM
        renderThemeTree();

        // Populate dynamic Year filters
        populateYearFilters();

        // Setup event listeners
        setupEventListeners();

        // Initial Filter & Render
        applyFilters();

    } catch (err) {
        console.error('Error loading files:', err);
        document.getElementById('table-body').innerHTML = `
            <tr>
                <td colspan="9" style="text-align:center; color: #f87171; padding: 2rem;">
                    Failed to load required data files.<br>
                    Make sure <strong>conferences_filtrees.csv</strong>, <strong>FoRcode_details.csv</strong>, and <strong>CORE_all26.csv</strong> are in the project folder.
                </td>
            </tr>`;
    }
}

function renderThemeTree() {
    const treeContainer = document.getElementById('theme-tree');
    treeContainer.innerHTML = '';

    // Sort groups alphabetically by name
    const sortedGroups = Object.values(themeGroups).sort((a, b) => a.name.localeCompare(b.name));

    sortedGroups.forEach(group => {
        if (group.subareas.length === 0) return; // Skip empty groups

        const groupNode = document.createElement('div');
        groupNode.className = 'tree-node group-node';

        const headerDiv = document.createElement('div');
        headerDiv.className = 'node-header';

        const toggleSpan = document.createElement('span');
        toggleSpan.className = 'toggle-icon';
        toggleSpan.textContent = '▶';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'group-checkbox';
        checkbox.dataset.code = group.code;

        const label = document.createElement('span');
        label.className = 'node-label';
        label.textContent = `${group.code} - ${group.name}`;

        headerDiv.appendChild(toggleSpan);
        headerDiv.appendChild(checkbox);
        headerDiv.appendChild(label);

        const childrenDiv = document.createElement('div');
        childrenDiv.className = 'node-children hidden';

        // Sort subareas alphabetically by name
        const sortedSubareas = group.subareas.sort((a, b) => a.name.localeCompare(b.name));

        sortedSubareas.forEach(sub => {
            const subNode = document.createElement('div');
            subNode.className = 'subarea-node';

            const subCheckbox = document.createElement('input');
            subCheckbox.type = 'checkbox';
            subCheckbox.className = 'subarea-checkbox';
            subCheckbox.dataset.code = sub.code;
            subCheckbox.dataset.parent = group.code;

            const subLabel = document.createElement('span');
            subLabel.className = 'node-label';
            subLabel.textContent = `${sub.code} - ${sub.name}`;

            subNode.appendChild(subCheckbox);
            subNode.appendChild(subLabel);
            childrenDiv.appendChild(subNode);

            // Subarea checkbox click handler
            subCheckbox.addEventListener('change', () => {
                updateGroupCheckboxState(group.code);
                applyFilters();
            });

            // Make clicking the label toggle the checkbox
            subLabel.addEventListener('click', (e) => {
                e.stopPropagation();
                subCheckbox.checked = !subCheckbox.checked;
                updateGroupCheckboxState(group.code);
                applyFilters();
            });
        });

        groupNode.appendChild(headerDiv);
        groupNode.appendChild(childrenDiv);
        treeContainer.appendChild(groupNode);

        // Group checkbox click handler
        checkbox.addEventListener('change', () => {
            const checked = checkbox.checked;
            childrenDiv.querySelectorAll('.subarea-checkbox').forEach(subCheck => {
                subCheck.checked = checked;
            });
            applyFilters();
        });

        // Toggle expand/collapse
        const toggleExpand = (e) => {
            if (e.target === checkbox) return; // Ignore clicks directly on checkbox
            const isHidden = childrenDiv.classList.contains('hidden');
            if (isHidden) {
                childrenDiv.classList.remove('hidden');
                toggleSpan.textContent = '▼';
            } else {
                childrenDiv.classList.add('hidden');
                toggleSpan.textContent = '▶';
            }
        };

        headerDiv.addEventListener('click', toggleExpand);
    });
}

// Update Group checkbox state based on children status (checked, unchecked, indeterminate)
function updateGroupCheckboxState(groupCode) {
    const groupNode = document.querySelector(`.group-checkbox[data-code="${groupCode}"]`);
    if (!groupNode) return;

    const children = document.querySelectorAll(`.subarea-checkbox[data-parent="${groupCode}"]`);
    let checkedCount = 0;
    
    children.forEach(c => {
        if (c.checked) checkedCount++;
    });

    if (checkedCount === 0) {
        groupNode.checked = false;
        groupNode.indeterminate = false;
    } else if (checkedCount === children.length) {
        groupNode.checked = true;
        groupNode.indeterminate = false;
    } else {
        groupNode.checked = false;
        groupNode.indeterminate = true;
    }
}

function populateYearFilters() {
    const yearOptions = document.getElementById('year-options');
    yearOptions.innerHTML = '';
    
    // Get unique years present in the CSV
    const years = [...new Set(allConferences.map(c => c.Year).filter(Boolean))].sort();
    
    if (years.length === 0) {
        yearOptions.innerHTML = '<div style="padding: 10px; color: var(--text-muted);">No years found</div>';
        return;
    }

    years.forEach(year => {
        const label = document.createElement('label');
        label.className = 'checkbox-container';
        
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = year;
        input.className = 'year-checkbox';
        input.checked = true;
        
        const span = document.createElement('span');
        span.textContent = year;
        
        label.appendChild(input);
        label.appendChild(span);
        yearOptions.appendChild(label);
        
        input.addEventListener('change', () => {
            updateYearSelectLabel();
            applyFilters();
        });
    });
    
    updateYearSelectLabel();
}

function setupEventListeners() {
    // Dropdown configurations
    const dropdowns = [
        { btn: 'theme-select-btn', menu: 'theme-dropdown-menu' },
        { btn: 'rank-select-btn', menu: 'rank-dropdown-menu' },
        { btn: 'year-select-btn', menu: 'year-dropdown-menu' },
        { btn: 'status-select-btn', menu: 'status-dropdown-menu' }
    ];

    // Toggle dropdown visibility
    dropdowns.forEach(d => {
        const btn = document.getElementById(d.btn);
        if (btn) {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                // Close other dropdowns
                dropdowns.forEach(other => {
                    if (other.btn !== d.btn) {
                        document.getElementById(other.menu).classList.add('hidden');
                        document.getElementById(other.btn).classList.remove('active');
                    }
                });
                
                // Toggle current
                const menu = document.getElementById(d.menu);
                menu.classList.toggle('hidden');
                btn.classList.toggle('active');
            });
        }
    });

    // Close dropdowns on outside click
    document.addEventListener('click', (e) => {
        dropdowns.forEach(d => {
            const menu = document.getElementById(d.menu);
            const btn = document.getElementById(d.btn);
            if (menu && btn && !menu.contains(e.target) && !btn.contains(e.target)) {
                menu.classList.add('hidden');
                btn.classList.remove('active');
            }
        });
    });

    // Theme Search input
    document.getElementById('theme-search').addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        const groupNodes = document.querySelectorAll('.group-node');

        groupNodes.forEach(groupNode => {
            const groupHeader = groupNode.querySelector('.node-header');
            const groupLabel = groupHeader.querySelector('.node-label').textContent.toLowerCase();
            const childrenDiv = groupNode.querySelector('.node-children');
            const subareaNodes = groupNode.querySelectorAll('.subarea-node');
            const toggleSpan = groupHeader.querySelector('.toggle-icon');

            let anyChildVisible = false;

            subareaNodes.forEach(subNode => {
                const subLabel = subNode.querySelector('.node-label').textContent.toLowerCase();
                if (subLabel.includes(query)) {
                    subNode.classList.remove('hidden');
                    anyChildVisible = true;
                } else {
                    subNode.classList.add('hidden');
                }
            });

            if (groupLabel.includes(query) || anyChildVisible) {
                groupNode.classList.remove('hidden');
                if (query !== '') {
                    childrenDiv.classList.remove('hidden');
                    toggleSpan.textContent = '▼';
                }
            } else {
                groupNode.classList.add('hidden');
            }
        });
    });

    // Rank checkbox change
    document.querySelectorAll('.rank-checkbox').forEach(chk => {
        chk.addEventListener('change', () => {
            updateRankSelectLabel();
            applyFilters();
        });
    });

    // Status checkbox change
    document.querySelectorAll('.status-checkbox').forEach(chk => {
        chk.addEventListener('change', () => {
            updateStatusSelectLabel();
            applyFilters();
        });
    });

    // Date filters change
    document.getElementById('date-depot-filter').addEventListener('change', applyFilters);
    document.getElementById('date-reponse-filter').addEventListener('change', applyFilters);

    // Setup interactive sorting headers
    document.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const column = th.dataset.sort;

            if (currentSortColumn === column) {
                currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                currentSortColumn = column;
                currentSortDirection = 'asc';
            }

            // Update column styling classes
            document.querySelectorAll('th[data-sort]').forEach(header => {
                header.classList.remove('sort-asc', 'sort-desc');
            });
            th.classList.add(currentSortDirection === 'asc' ? 'sort-asc' : 'sort-desc');

            applyFilters();
        });
    });

    // Reset button
    document.getElementById('reset-btn').addEventListener('click', () => {
        // Reset inputs
        document.getElementById('date-depot-filter').value = '';
        document.getElementById('date-reponse-filter').value = '';
        document.getElementById('theme-search').value = '';
        
        // Reset rank checkboxes (check all)
        document.querySelectorAll('.rank-checkbox').forEach(chk => chk.checked = true);
        updateRankSelectLabel();

        // Reset status checkboxes (check all)
        document.querySelectorAll('.status-checkbox').forEach(chk => chk.checked = true);
        updateStatusSelectLabel();

        // Reset year checkboxes (check all)
        document.querySelectorAll('.year-checkbox').forEach(chk => chk.checked = true);
        updateYearSelectLabel();

        // Reset theme checkboxes (uncheck all)
        document.querySelectorAll('.group-checkbox, .subarea-checkbox').forEach(chk => {
            chk.checked = false;
            chk.indeterminate = false;
        });
        updateThemeSelectLabel();

        // Reset search visibility
        const groupNodes = document.querySelectorAll('.group-node');
        groupNodes.forEach(groupNode => {
            groupNode.classList.remove('hidden');
            groupNode.querySelector('.node-children').classList.add('hidden');
            groupNode.querySelector('.toggle-icon').textContent = '▶';
            groupNode.querySelectorAll('.subarea-node').forEach(s => s.classList.remove('hidden'));
        });

        // Reset Sort state
        currentSortColumn = null;
        currentSortDirection = 'asc';
        document.querySelectorAll('th[data-sort]').forEach(header => {
            header.classList.remove('sort-asc', 'sort-desc');
        });

        applyFilters();
    });

    // Export button
    document.getElementById('export-btn').addEventListener('click', exportCSV);
}

function updateRankSelectLabel() {
    const checked = Array.from(document.querySelectorAll('.rank-checkbox:checked')).map(c => c.value);
    const label = document.getElementById('rank-select-label');
    
    if (checked.length === 4) {
        label.textContent = 'All Ranks';
    } else if (checked.length === 0) {
        label.textContent = 'No Ranks Selected';
    } else {
        label.textContent = checked.join(', ');
    }
}

function updateYearSelectLabel() {
    const checkboxes = document.querySelectorAll('.year-checkbox');
    const checked = Array.from(document.querySelectorAll('.year-checkbox:checked')).map(c => c.value);
    const label = document.getElementById('year-select-label');
    
    if (checked.length === checkboxes.length) {
        label.textContent = 'All Years';
    } else if (checked.length === 0) {
        label.textContent = 'No Years Selected';
    } else {
        label.textContent = checked.join(', ');
    }
}

function updateStatusSelectLabel() {
    const checkboxes = document.querySelectorAll('.status-checkbox');
    const checked = Array.from(document.querySelectorAll('.status-checkbox:checked')).map(c => {
        return c.value === 'found' ? 'Found' : 'Not Found';
    });
    const label = document.getElementById('status-select-label');
    
    if (checked.length === checkboxes.length) {
        label.textContent = 'All Statuses';
    } else if (checked.length === 0) {
        label.textContent = 'No Status Selected';
    } else {
        label.textContent = checked.join(', ');
    }
}

function updateThemeSelectLabel() {
    const selectedSubareas = Array.from(document.querySelectorAll('.subarea-checkbox:checked'));
    const label = document.getElementById('theme-select-label');

    if (selectedSubareas.length === 0) {
        label.textContent = 'All Themes';
    } else {
        label.textContent = `${selectedSubareas.length} Theme(s) Selected`;
    }
}

function sortData(column, direction) {
    const rankWeights = { 'A*': 4, 'A': 3, 'B': 2, 'C': 1 };

    filteredConferences.sort((a, b) => {
        let valA = a[column];
        let valB = b[column];

        // Rank weights comparison
        if (column === 'Rank') {
            const wA = rankWeights[valA] || 0;
            const wB = rankWeights[valB] || 0;
            return direction === 'asc' ? wA - wB : wB - wA;
        }

        // Push N/A, None, nan empty values to the bottom
        const isNA_A = !valA || valA === 'N/A' || valA === 'None' || valA === 'nan' || valA === '';
        const isNA_B = !valB || valB === 'N/A' || valB === 'None' || valB === 'nan' || valB === '';
        if (isNA_A && !isNA_B) return 1;
        if (!isNA_A && isNA_B) return -1;
        if (isNA_A && isNA_B) return 0;

        // Numeric comparison (Year)
        if (column === 'Year') {
            const numA = Number(valA);
            const numB = Number(valB);
            return direction === 'asc' ? numA - numB : numB - numA;
        }

        // Date comparison
        if (column === 'Submission Deadline' || column === 'Notification Date') {
            const dateA = new Date(valA);
            const dateB = new Date(valB);
            return direction === 'asc' ? dateA - dateB : dateB - dateA;
        }

        // Standard string comparison (Acronym, Name, Topics, Status, URL)
        const strA = String(valA).toLowerCase();
        const strB = String(valB).toLowerCase();
        return direction === 'asc' ? strA.localeCompare(strB) : strB.localeCompare(strA);
    });
}

function applyFilters() {
    updateThemeSelectLabel();

    const selectedRanks = Array.from(document.querySelectorAll('.rank-checkbox:checked')).map(c => c.value);
    const selectedYears = Array.from(document.querySelectorAll('.year-checkbox:checked')).map(c => c.value);
    const selectedStatuses = Array.from(document.querySelectorAll('.status-checkbox:checked')).map(c => c.value);
    
    // Get checked subarea codes and group codes
    const selectedSubareas = Array.from(document.querySelectorAll('.subarea-checkbox:checked')).map(c => c.dataset.code);
    const selectedGroups = Array.from(document.querySelectorAll('.group-checkbox:checked')).map(c => c.dataset.code);

    const isThemeFilterActive = selectedSubareas.length > 0 || selectedGroups.length > 0;

    const dateDepot = document.getElementById('date-depot-filter').value;
    const dateReponse = document.getElementById('date-reponse-filter').value;

    filteredConferences = allConferences.filter(conf => {
        // 1. Rank filter
        if (!selectedRanks.includes(conf.Rank)) {
            return false;
        }

        // 2. Year filter
        if (!selectedYears.includes(String(conf.Year))) {
            return false;
        }

        // 3. Status filter
        const isFound = conf.Status && conf.Status.toLowerCase().includes('success');
        const statusVal = isFound ? 'found' : 'notfound';
        if (!selectedStatuses.includes(statusVal)) {
            return false;
        }

        // 4. Theme filter
        if (isThemeFilterActive) {
            const acronym = (conf.Acronym || '').toLowerCase();
            const confCodes = acronymToForMap[acronym] || [];
            
            // Check if any of the conference's FoR codes are checked
            const hasMatch = confCodes.some(code => {
                // If it's a subarea code (6 chars)
                if (selectedSubareas.includes(code)) return true;
                
                // If the group code matches
                if (selectedGroups.includes(code)) return true;

                // If code is a subarea, check if its parent group is checked
                const parent = code.slice(0, 4);
                if (selectedGroups.includes(parent)) return true;

                return false;
            });

            if (!hasMatch) return false;
        }

        // 5. Submission Deadline filter
        if (dateDepot && conf['Submission Deadline']) {
            if (conf['Submission Deadline'] < dateDepot) return false;
        }

        // 6. Notification Date filter (On/Before)
        if (dateReponse && conf['Notification Date']) {
            if (conf['Notification Date'] > dateReponse) return false;
        }

        return true;
    });

    // Apply Sorting if active
    if (currentSortColumn) {
        sortData(currentSortColumn, currentSortDirection);
    }

    renderTable(filteredConferences);
}

function getRankBadgeClass(rank) {
    if (rank === 'A*') return 'rank-A-star';
    if (rank === 'A') return 'rank-A';
    if (rank === 'B') return 'rank-B';
    if (rank === 'C') return 'rank-C';
    return '';
}

function getStatusHtml(status) {
    if (!status || status === 'N/A') return '<span style="font-size: 0.85rem; color: var(--text-muted);">N/A</span>';
    const s = status.toLowerCase();
    if (s.includes('success')) {
        return `<span style="font-size: 0.85rem; color: #34d399; font-weight: 500;">${status}</span>`;
    } else if (s.includes('biennial') || s.includes('triennial')) {
        return `<span style="font-size: 0.85rem; color: #a78bfa; font-weight: 500;" title="Conference occurs cyclically. Automatically postponed to the next edition year.">${status}</span>`;
    } else if (s.includes('discontinued') || s.includes('inactive') || s.includes('archived')) {
        return `<span style="font-size: 0.85rem; color: #94a3b8; font-style: italic;" title="No active editions found in recent years. Protected by long verification cooldown.">${status}</span>`;
    } else {
        return `<span style="font-size: 0.85rem; color: #f87171;">${status}</span>`;
    }
}

function renderTable(data) {
    const tbody = document.getElementById('table-body');
    tbody.innerHTML = '';

    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding: 2rem; color: #94a3b8;">No conferences found matching your criteria.</td></tr>`;
        return;
    }

    data.forEach(conf => {
        const tr = document.createElement('tr');
        
        const rankBadge = conf.Rank && conf.Rank !== 'N/A' ? `<span class="badge ${getRankBadgeClass(conf.Rank)}">${conf.Rank}</span>` : '<span class="badge" style="background: rgba(255,255,255,0.1)">N/A</span>';
        
        let urlHtml = '<span style="color: #64748b;">N/A</span>';
        if (conf.URL && conf.URL !== 'N/A') {
            urlHtml = `<a href="${conf.URL}" target="_blank" class="link-btn">
                Visit
                <svg style="width: 14px; height: 14px;" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
            </a>`;
        }

        const topicsText = conf.Topics ? conf.Topics : 'N/A';
        const formattedTopics = topicsText.replace(/"/g, '&quot;');

        const githubEditUrl = `https://github.com/JohanPy/ICORESearch/edit/master/conferences_db.json`;
        const editHtml = `<a href="${githubEditUrl}" target="_blank" class="edit-btn" title="Suggest an edit on GitHub (opens conferences_db.json)">
            <svg style="width: 14px; height: 14px;" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg>
        </a>`;

        tr.innerHTML = `
            <td><strong>${conf.Acronym || 'N/A'}</strong></td>
            <td><span style="font-size: 0.85rem; color: var(--text-muted); font-weight: 500;">${conf.Year || 'N/A'}</span></td>
            <td>${conf.Name || 'N/A'}</td>
            <td>${rankBadge}</td>
            <td>${conf['Submission Deadline'] || 'N/A'}</td>
            <td>${conf['Notification Date'] || 'N/A'}</td>
            <td class="topics-cell" title="${formattedTopics}">${topicsText}</td>
            <td>
                ${getStatusHtml(conf.Status)}
            </td>
            <td>
                <div style="display: flex; align-items: center; gap: 8px;">
                    ${urlHtml}
                    ${editHtml}
                </div>
            </td>
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

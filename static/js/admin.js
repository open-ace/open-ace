/**
 * Administration JavaScript Module
 *
 * Handles user management and quota management UI.
 */

const Admin = (function() {
    // Use window.Auth to safely reference the global Auth object
    const getAuth = () => typeof window.Auth !== 'undefined' ? window.Auth : null;

    // Get all users
    async function getUsers() {
        const auth = getAuth();
        const token = auth ? auth.getSessionToken() : null;
        if (!token) return { success: false, error: 'No session token' };

        try {
            const response = await fetch('/api/admin/users', {
                method: 'GET',
                headers: {
                    'Authorization': 'Bearer ' + token,
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            return { success: response.ok, data: data };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Create a new user
    async function createUser(userData) {
        const auth = getAuth();
        const token = auth ? auth.getSessionToken() : null;
        if (!token) return { success: false, error: 'No session token' };

        try {
            const response = await fetch('/api/admin/users', {
                method: 'POST',
                headers: {
                    'Authorization': 'Bearer ' + token,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(userData)
            });

            const data = await response.json();
            return { success: response.ok, data: data };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Update user information
    async function updateUser(userId, userData) {
        const auth = getAuth();
        const token = auth ? auth.getSessionToken() : null;
        if (!token) return { success: false, error: 'No session token' };

        try {
            const response = await fetch(`/api/admin/users/${userId}`, {
                method: 'PUT',
                headers: {
                    'Authorization': 'Bearer ' + token,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(userData)
            });

            const data = await response.json();
            return { success: response.ok, data: data };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Delete a user
    async function deleteUser(userId) {
        const auth = getAuth();
        const token = auth ? auth.getSessionToken() : null;
        if (!token) return { success: false, error: 'No session token' };

        try {
            const response = await fetch(`/api/admin/users/${userId}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': 'Bearer ' + token
                }
            });

            const data = await response.json();
            return { success: response.ok, data: data };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Update user quota
    async function updateUserQuota(userId, quotaTokens, quotaRequests) {
        const auth = getAuth();
        const token = auth ? auth.getSessionToken() : null;
        if (!token) return { success: false, error: 'No session token' };

        try {
            const response = await fetch(`/api/admin/users/${userId}/quota`, {
                method: 'PUT',
                headers: {
                    'Authorization': 'Bearer ' + token,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    quota_tokens: quotaTokens,
                    quota_requests: quotaRequests
                })
            });

            const data = await response.json();
            return { success: response.ok, data: data };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Get quota usage statistics
    async function getQuotaUsage(startDate, endDate) {
        const auth = getAuth();
        const token = auth ? auth.getSessionToken() : null;
        if (!token) return { success: false, error: 'No session token' };

        try {
            const url = `/api/admin/quota/usage?start=${startDate}&end=${endDate}`;
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Authorization': 'Bearer ' + token
                }
            });

            const data = await response.json();
            return { success: response.ok, data: data };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Format token count
    function formatTokens(tokens) {
        if (!tokens) return '0';
        if (tokens >= 1000000000) return (tokens / 1000000000).toFixed(2) + 'B';
        if (tokens >= 1000000) return (tokens / 1000000).toFixed(2) + 'M';
        if (tokens >= 1000) return (tokens / 1000).toFixed(2) + 'K';
        return tokens.toString();
    }

    // Render users table
    async function renderUsersTable() {
        const result = await getUsers();
        const tableBody = document.getElementById('users-table-body');

        if (!result.success) {
            tableBody.innerHTML = `<tr><td colspan="6" class="text-danger">${result.error}</td></tr>`;
            return;
        }

        const users = result.data.users || [];
        if (users.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="6" class="text-muted">No users found</td></tr>`;
            return;
        }

        let html = '';
        users.forEach(user => {
            const isActive = user.is_active === 1 || user.is_active === true;
            const roleBadge = user.role === 'admin' ?
                '<span class="badge bg-danger">Admin</span>' :
                '<span class="badge bg-primary">User</span>';
            const statusBadge = isActive ?
                '<span class="badge bg-success">Active</span>' :
                '<span class="badge bg-secondary">Inactive</span>';

            html += `
                <tr>
                    <td>${user.id}</td>
                    <td>${escapeHtml(user.username)}</td>
                    <td>${escapeHtml(user.email || '')}</td>
                    <td>${roleBadge}</td>
                    <td>${formatTokens(user.quota_tokens)}</td>
                    <td>${statusBadge}</td>
                    <td>
                        <button class="btn btn-sm btn-warning edit-user-btn" data-id="${user.id}">
                            <i class="bi bi-pencil"></i> Edit
                        </button>
                        <button class="btn btn-sm btn-danger delete-user-btn" data-id="${user.id}">
                            <i class="bi bi-trash"></i> Delete
                        </button>
                    </td>
                </tr>
            `;
        });
        tableBody.innerHTML = html;

        // Add event listeners for edit/delete buttons
        document.querySelectorAll('.edit-user-btn').forEach(btn => {
            btn.addEventListener('click', () => editUser(btn.dataset.id));
        });

        document.querySelectorAll('.delete-user-btn').forEach(btn => {
            btn.addEventListener('click', () => confirmDeleteUser(btn.dataset.id));
        });
    }

    // Escape HTML to prevent XSS
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Show modal for adding new user
    function showAddUserModal() {
        const modal = document.getElementById('user-modal');
        if (!modal) return;

        const modalTitle = modal.querySelector('.modal-title');
        const form = modal.querySelector('form');
        const userIdInput = modal.querySelector('#user-id');
        const usernameInput = modal.querySelector('#username');
        const emailInput = modal.querySelector('#email');
        const roleSelect = modal.querySelector('#role');
        const quotaTokensInput = modal.querySelector('#quota_tokens');
        const quotaRequestsInput = modal.querySelector('#quota_requests');
        const isActiveSelect = modal.querySelector('#is_active');

        modalTitle.textContent = 'Add New User';
        userIdInput.value = '';
        usernameInput.value = '';
        emailInput.value = '';
        roleSelect.value = 'user';
        quotaTokensInput.value = 1000000;
        quotaRequestsInput.value = 1000;
        isActiveSelect.value = '1';

        modal.style.display = 'block';
    }

    // Show modal for editing user
    async function editUser(userId) {
        const modal = document.getElementById('user-modal');
        if (!modal) return;

        const modalTitle = modal.querySelector('.modal-title');
        const form = modal.querySelector('form');
        const userIdInput = modal.querySelector('#user-id');
        const usernameInput = modal.querySelector('#username');
        const emailInput = modal.querySelector('#email');
        const roleSelect = modal.querySelector('#role');
        const quotaTokensInput = modal.querySelector('#quota_tokens');
        const quotaRequestsInput = modal.querySelector('#quota_requests');
        const isActiveSelect = modal.querySelector('#is_active');

        // Get user data
        const result = await getUsers();
        if (!result.success) {
            alert(result.error);
            return;
        }

        const user = result.data.users.find(u => u.id === parseInt(userId));
        if (!user) {
            alert('User not found');
            return;
        }

        modalTitle.textContent = 'Edit User';
        userIdInput.value = user.id;
        usernameInput.value = user.username;
        emailInput.value = user.email || '';
        roleSelect.value = user.role;
        quotaTokensInput.value = user.quota_tokens;
        quotaRequestsInput.value = user.quota_requests;
        isActiveSelect.value = user.is_active === 1 ? '1' : '0';

        modal.style.display = 'block';
    }

    // Handle user form submission
    async function handleUserSubmit(event) {
        event.preventDefault();
        const form = event.target;

        const userId = form.querySelector('#user-id').value;
        const username = form.querySelector('#username').value;
        const email = form.querySelector('#email').value;
        const role = form.querySelector('#role').value;
        const quotaTokens = parseInt(form.querySelector('#quota_tokens').value) || 0;
        const quotaRequests = parseInt(form.querySelector('#quota_requests').value) || 0;
        const isActive = form.querySelector('#is_active').value === '1';

        const userData = {
            username: username,
            email: email,
            role: role,
            quota_tokens: quotaTokens,
            quota_requests: quotaRequests,
            is_active: isActive
        };

        let result;
        if (userId) {
            result = await updateUser(userId, userData);
        } else {
            result = await createUser(userData);
        }

        if (result.success) {
            const modal = document.getElementById('user-modal');
            modal.style.display = 'none';
            renderUsersTable();
            alert('User saved successfully!');
        } else {
            alert('Error: ' + result.error);
        }
    }

    // Confirm and delete user
    async function confirmDeleteUser(userId) {
        if (!confirm('Are you sure you want to delete this user? This action cannot be undone.')) {
            return;
        }

        const result = await deleteUser(userId);
        if (result.success) {
            renderUsersTable();
            alert('User deleted successfully!');
        } else {
            alert('Error: ' + result.error);
        }
    }

    // Render quota usage statistics
    async function renderQuotaUsage() {
        const today = new Date().toISOString().split('T')[0];
        const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];

        const result = await getQuotaUsage(weekAgo, today);
        const container = document.getElementById('quota-usage-container');

        if (!result.success) {
            container.innerHTML = `<div class="alert alert-danger">${result.error}</div>`;
            return;
        }

        const usage = result.data || {};
        container.innerHTML = `
            <div class="row">
                <div class="col-md-4">
                    <div class="card">
                        <div class="card-body">
                            <h6 class="card-title">Total Tokens</h6>
                            <h3 class="text-primary">${formatTokens(usage.total_tokens || 0)}</h3>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card">
                        <div class="card-body">
                            <h6 class="card-title">Total Requests</h6>
                            <h3 class="text-success">${(usage.total_requests || 0).toLocaleString()}</h3>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card">
                        <div class="card-body">
                            <h6 class="card-title">Active Users</h6>
                            <h3 class="text-warning">${usage.active_users || 0}</h3>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    // Initialize admin module
    function init() {
        // Render users table on load
        if (document.getElementById('users-table-body')) {
            renderUsersTable();
        }

        // Render quota usage on load
        if (document.getElementById('quota-usage-container')) {
            renderQuotaUsage();
        }

        // Add event listener for Add User button
        const addUserBtn = document.getElementById('add-user-btn');
        if (addUserBtn) {
            addUserBtn.addEventListener('click', showAddUserModal);
        }

        // Add event listener for user form submission
        const userForm = document.getElementById('user-form');
        if (userForm) {
            userForm.addEventListener('submit', handleUserSubmit);
        }
    }

    // Export public functions
    return {
        init,
        getUsers,
        createUser,
        updateUser,
        deleteUser,
        updateUserQuota,
        getQuotaUsage,
        renderUsersTable,
        renderQuotaUsage
    };
})();

// Initialize admin when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('users-table-body') || document.getElementById('quota-usage-container')) {
        Admin.init();
    }
});

import { useState, useEffect } from "react";
import {
  FolderIcon,
  UsersIcon,
  ClockIcon,
  ChartBarIcon,
  TrashIcon,
  EyeIcon,
} from "@heroicons/react/24/outline";
import {
  getAllProjectStats,
  type ProjectStats,
  deleteProject,
} from "@/api/projects";
import { ConfirmModal } from "@/components/ConfirmModal";

export function ProjectManagement() {
  const [stats, setStats] = useState<ProjectStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<ProjectStats | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectStats | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadStats = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await getAllProjectStats();
      setStats(response.stats || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load project stats");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStats();
  }, []);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    
    setIsDeleting(true);
    try {
      await deleteProject(deleteTarget.project_id);
      await loadStats();
      setDeleteTarget(null);
    } catch (err) {
      console.error("Failed to delete project:", err);
    } finally {
      setIsDeleting(false);
    }
  };

  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  };

  const formatNumber = (num: number): string => {
    if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
    return num.toString();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-slate-500 dark:text-slate-400">Loading projects...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <div className="text-red-500 dark:text-red-400">{error}</div>
        <button
          onClick={loadStats}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
            Project Management
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            View and manage projects, track usage and collaboration
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-sm text-slate-500 dark:text-slate-400">
            {stats.length} project{stats.length !== 1 ? "s" : ""}
          </div>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg">
              <FolderIcon className="h-5 w-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                {stats.length}
              </div>
              <div className="text-sm text-slate-500 dark:text-slate-400">
                Total Projects
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-green-100 dark:bg-green-900/30 rounded-lg">
              <UsersIcon className="h-5 w-5 text-green-600 dark:text-green-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                {formatNumber(stats.reduce((sum, p) => sum + p.total_users, 0))}
              </div>
              <div className="text-sm text-slate-500 dark:text-slate-400">
                Total Users
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-purple-100 dark:bg-purple-900/30 rounded-lg">
              <ChartBarIcon className="h-5 w-5 text-purple-600 dark:text-purple-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                {formatNumber(stats.reduce((sum, p) => sum + p.total_tokens, 0))}
              </div>
              <div className="text-sm text-slate-500 dark:text-slate-400">
                Total Tokens
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-yellow-100 dark:bg-yellow-900/30 rounded-lg">
              <ClockIcon className="h-5 w-5 text-yellow-600 dark:text-yellow-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                {formatDuration(stats.reduce((sum, p) => sum + p.total_duration_seconds, 0))}
              </div>
              <div className="text-sm text-slate-500 dark:text-slate-400">
                Total Work Time
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Project List */}
      <div className="bg-white dark:bg-slate-800 rounded-lg shadow overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            Projects
          </h2>
        </div>

        {stats.length === 0 ? (
          <div className="px-6 py-12 text-center text-slate-500 dark:text-slate-400">
            No projects found. Projects will appear here when users create them
            from the Work mode.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-50 dark:bg-slate-700/50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    Project
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    Users
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    Tokens
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    Requests
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    Work Time
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    Last Active
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                {stats.map((project) => (
                  <tr
                    key={project.project_id}
                    className="hover:bg-slate-50 dark:hover:bg-slate-700/50"
                  >
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-3">
                        <FolderIcon className="h-5 w-5 text-yellow-500" />
                        <div>
                          <div className="font-medium text-slate-900 dark:text-slate-100">
                            {project.project_name || project.project_path.split(/[/\\]/).pop()}
                          </div>
                          <div className="text-sm text-slate-500 dark:text-slate-400 font-mono truncate max-w-xs">
                            {project.project_path}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-1">
                        <UsersIcon className="h-4 w-4 text-slate-400" />
                        <span className="text-slate-900 dark:text-slate-100">
                          {project.total_users}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-slate-900 dark:text-slate-100">
                      {formatNumber(project.total_tokens)}
                    </td>
                    <td className="px-6 py-4 text-slate-900 dark:text-slate-100">
                      {formatNumber(project.total_requests)}
                    </td>
                    <td className="px-6 py-4 text-slate-900 dark:text-slate-100">
                      {formatDuration(project.total_duration_seconds)}
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-500 dark:text-slate-400">
                      {project.last_access
                        ? new Date(project.last_access).toLocaleDateString()
                        : "Never"}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => setSelectedProject(project)}
                          className="p-2 text-slate-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg"
                          title="View details"
                        >
                          <EyeIcon className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => setDeleteTarget(project)}
                          className="p-2 text-slate-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg"
                          title="Delete project"
                        >
                          <TrashIcon className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Project Detail Modal */}
      {selectedProject && (
        <ProjectDetailModal
          project={selectedProject}
          onClose={() => setSelectedProject(null)}
        />
      )}

      {/* Delete Confirmation Modal */}
      <ConfirmModal
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete Project"
        message={`Are you sure you want to delete "${deleteTarget?.project_name || deleteTarget?.project_path}"? This will remove all associated data but won't affect the actual directory.`}
        confirmText="Delete"
        variant="danger"
        isLoading={isDeleting}
      />
    </div>
  );
}

// Project Detail Modal Component
function ProjectDetailModal({
  project,
  onClose,
}: {
  project: ProjectStats;
  onClose: () => void;
}) {
  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      <div className="flex min-h-full items-center justify-center p-4">
        <div
          className="fixed inset-0 bg-black/50"
          onClick={onClose}
        />
        <div className="relative bg-white dark:bg-slate-800 rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] overflow-y-auto">
          <div className="sticky top-0 bg-white dark:bg-slate-800 px-6 py-4 border-b border-slate-200 dark:border-slate-700">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                {project.project_name || "Project Details"}
              </h3>
              <button
                onClick={onClose}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              >
                ✕
              </button>
            </div>
          </div>

          <div className="px-6 py-4 space-y-6">
            {/* Path */}
            <div>
              <div className="text-sm text-slate-500 dark:text-slate-400">
                Path
              </div>
              <div className="font-mono text-sm text-slate-900 dark:text-slate-100 mt-1">
                {project.project_path}
              </div>
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-3">
                <div className="text-sm text-slate-500 dark:text-slate-400">
                  Users
                </div>
                <div className="text-xl font-bold text-slate-900 dark:text-slate-100">
                  {project.total_users}
                </div>
              </div>
              <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-3">
                <div className="text-sm text-slate-500 dark:text-slate-400">
                  Sessions
                </div>
                <div className="text-xl font-bold text-slate-900 dark:text-slate-100">
                  {project.total_sessions}
                </div>
              </div>
              <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-3">
                <div className="text-sm text-slate-500 dark:text-slate-400">
                  Tokens
                </div>
                <div className="text-xl font-bold text-slate-900 dark:text-slate-100">
                  {project.total_tokens.toLocaleString()}
                </div>
              </div>
              <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-3">
                <div className="text-sm text-slate-500 dark:text-slate-400">
                  Work Time
                </div>
                <div className="text-xl font-bold text-slate-900 dark:text-slate-100">
                  {formatDuration(project.total_duration_seconds)}
                </div>
              </div>
            </div>

            {/* Users List */}
            <div>
              <h4 className="text-sm font-medium text-slate-900 dark:text-slate-100 mb-3">
                Collaborators
              </h4>
              {project.user_stats.length === 0 ? (
                <div className="text-sm text-slate-500 dark:text-slate-400">
                  No users yet
                </div>
              ) : (
                <div className="space-y-2">
                  {project.user_stats.map((user: { id: number; user_id: number; username?: string; last_access_at: string; total_duration_seconds: number; total_tokens: number }) => (
                    <div
                      key={user.id}
                      className="flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-700/50 rounded-lg"
                    >
                      <div>
                        <div className="font-medium text-slate-900 dark:text-slate-100">
                          {user.username || `User ${user.user_id}`}
                        </div>
                        <div className="text-sm text-slate-500 dark:text-slate-400">
                          Last active: {new Date(user.last_access_at).toLocaleDateString()}
                        </div>
                      </div>
                      <div className="text-right text-sm">
                        <div className="text-slate-900 dark:text-slate-100">
                          {formatDuration(user.total_duration_seconds)}
                        </div>
                        <div className="text-slate-500 dark:text-slate-400">
                          {user.total_tokens.toLocaleString()} tokens
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
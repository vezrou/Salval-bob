import json
import httpx
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import main
from agents.github_fetcher import _parse_repo_url, _should_fetch, fetch_repo
from agents.subagents.analyzer import _parse_snapshot, analyze
from agents.repository_scope import file_scope


SNAPSHOT = {
    "stack": ["React", "TypeScript"], "components": ["Button (src/Button.tsx)"],
    "hooks": [], "utilities": [], "css_tokens": ["--primary"], "patterns": [],
    "summary": "A React project with a reusable Button.",
    "backend_integration": [],
}


class ProjectFlowTests(unittest.TestCase):
    def setUp(self):
        main._sessions.clear()
        self.client = TestClient(main.app)

    def test_analysis_then_every_agent_receives_context_and_followup_history(self):
        with patch('main.fetch_repo', return_value=[{"path": "src/Button.tsx", "content": "export const Button = () => null"}]), patch('main.analyze', return_value=SNAPSHOT):
            response = self.client.post('/analyze', json={"repo_url": "https://github.com/acme/ui"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('source_files', response.json()['context'])
        sid = response.json()['session_id']
        for route, module in [('code', 'coder'), ('debug', 'debugger'), ('ui', 'ui_ux'), ('architect', 'architect'), ('frontend', 'frontend')]:
            with self.subTest(route=route), patch('agents.main_agent.classify', return_value=route), patch(f'agents.subagents.{module}.generate', return_value='Reuse Button.') as generate:
                response = self.client.post('/build', json={"command": "Review my code and tell me what to improve", "session_id": sid})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['routed_to'], route)
                self.assertIn('src/Button.tsx', generate.call_args.args[1])
                self.assertIn('1: export const Button = () => null', generate.call_args.args[1])
                self.assertIn('Start reviewing it immediately', generate.call_args.args[0])
                self.assertIn('Plan', generate.call_args.args[0])
                self.assertIn('Do not produce a standalone backend audit', generate.call_args.args[0])
        self.assertEqual(len(main._sessions[sid]['history']), 10)

    def test_failed_analysis_does_not_replace_existing_context(self):
        main._sessions['existing'] = {"context": SNAPSHOT, "history": []}
        with patch('main.fetch_repo', return_value=[]), patch('main.analyze', side_effect=RuntimeError('Please retry')):
            response = self.client.post('/analyze', json={"repo_url": "https://github.com/acme/ui", "session_id": "existing"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(main._sessions['existing']['context'], SNAPSHOT)

    def test_unknown_session_is_not_silently_recreated(self):
        response = self.client.post('/build', json={"command": "Use my Button", "session_id": "expired"})
        self.assertEqual(response.status_code, 409)

    def test_general_chat_still_works(self):
        with patch('agents.main_agent.classify', return_value='code'), patch('agents.subagents.coder.generate', return_value='General advice') as generate:
            response = self.client.post('/build', json={"command": "Explain CSS"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('No repository has been analyzed', generate.call_args.args[1])

    def test_mixed_repo_preserves_frontend_and_backend_roles(self):
        tree = [{'type': 'blob', 'path': path} for path in ['frontend/src/api/client.ts', 'backend/main.py', 'backend/routes.ts']]
        with patch('agents.github_fetcher._default_branch', return_value='main'), patch('agents.github_fetcher._file_tree', return_value=tree), patch('agents.github_fetcher._fetch_file', return_value='sample source'):
            files = fetch_repo('https://github.com/acme/ui')
        self.assertEqual([(f['path'], f['scope']) for f in files], [
            ('frontend/src/api/client.ts', 'frontend'), ('backend/main.py', 'backend'), ('backend/routes.ts', 'backend')])
        with patch('agents.subagents.analyzer.generate', return_value=json.dumps(SNAPSHOT)) as generate:
            snapshot = analyze(files)
        self.assertEqual(snapshot['scope'], {'focus': 'frontend', 'frontend_files': 1, 'backend_files': 2})
        self.assertIn('[role: backend]', generate.call_args.args[1])
        self.assertIn('ONLY to understand integration', generate.call_args.args[0])

    def test_framework_routes_are_backend_but_api_clients_are_frontend(self):
        for path in ['src/app/api/users/route.ts', 'pages/api/users.ts', 'src/routes/+server.ts', 'api/handler.js']:
            self.assertEqual(file_scope(path), 'backend')
        self.assertEqual(file_scope('frontend/src/api/client.ts'), 'frontend')
        self.assertEqual(file_scope('src/app/page.tsx'), 'frontend')

    def test_backend_sampling_does_not_displace_frontend(self):
        tree = [{'type': 'blob', 'path': f'backend/route{i}.py'} for i in range(70)]
        tree.append({'type': 'blob', 'path': 'frontend/App.tsx'})
        with patch('agents.github_fetcher._default_branch', return_value='main'), patch('agents.github_fetcher._file_tree', return_value=tree), patch('agents.github_fetcher._fetch_file', return_value='source'):
            files = fetch_repo('https://github.com/acme/ui')
        self.assertEqual(sum(f['scope'] == 'backend' for f in files), 12)
        self.assertEqual(files[0]['path'], 'frontend/App.tsx')

    def test_github_rate_limit_has_actionable_message(self):
        request = httpx.Request('GET', 'https://api.github.com/repos/acme/ui')
        for status, headers, body, expected in [
            (403, {'x-ratelimit-remaining': '0'}, '', 429),
            (403, {}, 'API rate limit exceeded', 429),
            (429, {}, '', 429),
            (403, {}, 'Forbidden', 403),
        ]:
            response = httpx.Response(status, headers=headers, text=body, request=request)
            error = httpx.HTTPStatusError('GitHub failure', request=request, response=response)
            with self.subTest(status=status, body=body), patch('main.fetch_repo', side_effect=error):
                result = self.client.post('/analyze', json={'repo_url': 'https://github.com/acme/ui'})
                self.assertEqual(result.status_code, expected)
                if expected == 429:
                    self.assertIn('GITHUB_TOKEN', result.json()['detail'])

    def test_invalid_snapshot_is_an_error(self):
        for raw in ['not json', '{}', '{"stack": null}', json.dumps({**SNAPSHOT, 'hooks': 'useAuth'})]:
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                _parse_snapshot(raw)
        self.assertEqual(_parse_snapshot(json.dumps(SNAPSHOT)), SNAPSHOT)

    def test_url_validation(self):
        self.assertEqual(_parse_repo_url('https://github.com/acme/ui.git'), ('acme', 'ui'))
        for url in ['https://evilgithub.com/acme/ui', 'https://github.com/acme/ui/tree/main', 'file:///acme/ui']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _parse_repo_url(url)

    def test_manifest_included_dependencies_excluded(self):
        self.assertTrue(_should_fetch({'type': 'blob', 'path': 'package.json', 'size': 100}))
        self.assertFalse(_should_fetch({'type': 'blob', 'path': 'node_modules/lib/index.js'}))

    def test_all_downloads_failed_is_an_error(self):
        with patch('agents.github_fetcher._default_branch', return_value='main'), patch('agents.github_fetcher._file_tree', return_value=[{'type': 'blob', 'path': 'src/App.tsx'}]), patch('agents.github_fetcher._fetch_file', return_value=None), self.assertRaises(RuntimeError):
            fetch_repo('https://github.com/acme/ui')


if __name__ == '__main__':
    unittest.main()

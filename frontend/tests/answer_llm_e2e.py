"""Isolated UI/backend answer setup regression with a synthetic HTTP provider.

Run Vite on a temporary port and an empty QA backend with MemorySecrets, then:
  .venv/bin/python frontend/tests/answer_llm_e2e.py --base-url http://127.0.0.1:1431 --backend-url http://127.0.0.1:8766

Provider responses are simulated. This does not certify real external LLM access
or macOS Keychain, change production candidate data, or submit an application.
"""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


class SyntheticProvider(BaseHTTPRequestHandler):
    calls = []
    fail = False

    def log_message(self, *_args):
        pass

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200, {'data': [{'id': name, 'object': 'model'} for name in ('synthetic-ui-model', 'synthetic-ui-fallback')]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.calls.append(payload)
        if self.fail:
            self.reply(401, {'error': {'message': 'Synthetic invalid credential'}})
            return
        result = {'question_id': 'connection-probe', 'intent': 'CONNECTION_TEST', 'answer': 'Sample Candidate', 'facts_used': ['connection-probe'], 'selected_options': [], 'selected_roles': [], 'company_claims': [], 'claims': [{'text': 'Sample Candidate', 'fact_ids': ['connection-probe']}], 'evidence_quality': 'HIGH', 'word_count': 2, 'follows_instructions': True, 'answers_question': True, 'unsupported_claims': [], 'requires_human_input': False, 'missing_facts': []}
        if self.path.endswith('/responses'):
            self.reply(200, {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}],
                             'usage': {'input_tokens': 100, 'output_tokens': 25, 'input_tokens_details': {'cached_tokens': 20}}})
            return
        self.reply(200, {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}],
                         'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:1431')
    parser.add_argument('--backend-url', default='http://127.0.0.1:8766')
    parser.add_argument('--provider', choices=['openai-compatible', 'openai'], default='openai-compatible')
    args = parser.parse_args()
    native_openai = args.provider == 'openai'
    primary_rates = {'input_cost_per_million': .1, 'output_cost_per_million': .2}
    fallback_rates = {'input_cost_per_million': .5, 'output_cost_per_million': 1.1}
    if native_openai:
        primary_rates.update(cached_input_cost_per_million=.1, cache_write_cost_per_million=2.5)
        fallback_rates.update(cached_input_cost_per_million=.02, cache_write_cost_per_million=0)
    token = {'Authorization': 'Bearer meridian-development-token'}
    out = Path(tempfile.gettempdir()) / 'meridian-answer-ui-proof'
    out.mkdir(exist_ok=True)
    provider = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticProvider)
    thread = Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.on('pageerror', lambda error: errors.append(str(error)))
            health = await page.request.get(args.backend_url + '/api/health', headers=token)
            assert health.ok
            assert 'answer-ui-qa' in (await health.json())['data_dir'], 'Refusing to modify a non-QA workspace'
            reset = await page.request.patch(args.backend_url + '/api/settings', headers=token, data={'text_provider': 'none', 'text_model': '', 'text_fallback_model': '', 'text_base_url': '', 'monthly_budget': 0})
            assert reset.ok
            legacy_prices = {'input_cost_per_million': 9, 'output_cost_per_million': 10, 'answer_model_prices': {}}
            seed = await page.request.put(args.backend_url + '/api/integrations/' + args.provider, headers=token, data={'config': legacy_prices})
            assert seed.ok

            async def local_proxy(route):
                request = route.request
                path = urlparse(request.url).path
                query = urlparse(request.url).query
                response = await page.request.fetch(args.backend_url + path + ('?' + query if query else ''),
                    method=request.method, data=request.post_data_buffer, headers={**token, **({'Content-Type': 'application/json'} if request.post_data_buffer else {})})
                await route.fulfill(response=response)
            await page.route('**/api/**', local_proxy)
            print('Checking answer provider setup and discovery', flush=True)
            await page.goto(args.base_url)
            await page.get_by_role('navigation').get_by_role('button', name='Settings', exact=True).click()
            await page.get_by_role('button', name='AI & providers', exact=True).click()
            panel = page.locator('.panel').filter(has=page.get_by_role('heading', name='Application answer LLM', exact=True))
            await panel.get_by_text('Answer model setup required', exact=True).wait_for()
            await panel.get_by_text('An LLM is required to generate personalised answers to open-ended application questions.', exact=True).wait_for()
            await panel.get_by_label('Answer provider').select_option(args.provider)
            assert await panel.get_by_label('Input price per million tokens (USD)', exact=True).input_value() == '', 'Legacy provider rates leaked into model pricing'
            assert await panel.get_by_label('Output price per million tokens (USD)', exact=True).input_value() == ''
            assert await panel.get_by_label('Cached input price per million tokens (USD)', exact=True).count() == int(native_openai)
            assert await panel.get_by_label('Cache write price per million tokens (USD)', exact=True).count() == int(native_openai)
            await panel.get_by_label('Provider endpoint', exact=True).fill(f'http://127.0.0.1:{provider.server_port}/v1')
            await panel.get_by_label('API key', exact=True).fill('synthetic-ui-secret')
            await panel.get_by_role('button', name='Save and discover models', exact=True).click()
            await panel.get_by_text('2 available models discovered. Select a model and save the configuration.', exact=True).wait_for()
            assert await panel.get_by_label('API key', exact=True).input_value() == ''
            assert await panel.locator('datalist option').all_text_contents() == ['synthetic-ui-model', 'synthetic-ui-fallback']
            await panel.get_by_label('Answer model', exact=True).fill('synthetic-ui-model')
            await panel.get_by_label('Temperature', exact=True).fill('0.3')
            await panel.get_by_label('Maximum output tokens', exact=True).fill('2400')
            await panel.get_by_label('Request timeout (seconds)', exact=True).fill('30')
            await panel.get_by_label('Retry attempts', exact=True).fill('1')
            await panel.get_by_label('Concurrent question generation', exact=True).fill('2')
            await panel.get_by_label('Monthly AI usage limit (USD)', exact=True).fill('10')
            await panel.get_by_label('Input price per million tokens (USD)', exact=True).fill('0.1')
            await panel.get_by_label('Output price per million tokens (USD)', exact=True).fill('0.2')
            if native_openai:
                call_count = len(SyntheticProvider.calls)
                await panel.get_by_role('button', name='Save and test connection', exact=True).click()
                await panel.get_by_text('OpenAI also requires nonnegative cached input and cache write rates.', exact=False).wait_for()
                assert len(SyntheticProvider.calls) == call_count, 'Missing OpenAI cache rates triggered inference'
                await panel.get_by_label('Cached input price per million tokens (USD)', exact=True).fill('0.1')
                await panel.get_by_label('Cache write price per million tokens (USD)', exact=True).fill('2.5')
            print('Checking answer provider connection probe', flush=True)
            await panel.get_by_role('button', name='Save and test connection', exact=True).click()
            await panel.get_by_text('The configured provider returned a response. Review the connection test details below.', exact=True).wait_for()
            config = await (await page.request.get(args.backend_url + '/api/settings', headers=token)).json()
            assert config['text_model'] == 'synthetic-ui-model' and config['text_temperature'] == 0.3
            assert config['text_max_output_tokens'] == 2400 and config['text_retry_attempts'] == 1
            integrations = await (await page.request.get(args.backend_url + '/api/integrations', headers=token)).json()
            provider_config = next(i['config'] for i in integrations['items'] if i['provider'] == args.provider)
            assert provider_config['answer_model_prices']['synthetic-ui-model'] == primary_rates
            assert provider_config['input_cost_per_million'] == 9 and provider_config['output_cost_per_million'] == 10, 'Legacy provider prices changed'
            assert 'synthetic-ui-secret' not in json.dumps(integrations), 'Provider response exposed a credential'
            assert 'synthetic-ui-secret' not in await page.locator('body').inner_text()
            assert not await page.evaluate('Object.values(localStorage).some(v => v.includes("synthetic-ui-secret"))')
            assert SyntheticProvider.calls and SyntheticProvider.calls[-1]['model'] == 'synthetic-ui-model'
            prompt = json.dumps(SyntheticProvider.calls[-1])
            assert 'connection-probe' in prompt and 'Sample Candidate' in prompt
            assert 'Alejandro Newport' not in prompt, 'Synthetic connection probe leaked candidate information'
            if native_openai:
                assert SyntheticProvider.calls[-1]['store'] is False
                assert SyntheticProvider.calls[-1]['text']['format']['type'] == 'json_schema'

            print('Checking exact primary and fallback model pricing', flush=True)
            call_count = len(SyntheticProvider.calls)
            await panel.get_by_label('Answer model', exact=True).fill('synthetic-ui-unpriced')
            assert await panel.get_by_label('Input price per million tokens (USD)', exact=True).input_value() == ''
            assert await panel.get_by_label('Output price per million tokens (USD)', exact=True).input_value() == ''
            if native_openai:
                assert await panel.get_by_label('Cached input price per million tokens (USD)', exact=True).input_value() == ''
                assert await panel.get_by_label('Cache write price per million tokens (USD)', exact=True).input_value() == ''
            await panel.get_by_role('button', name='Save and test connection', exact=True).click()
            await panel.get_by_text('Enter current input and output token prices for the primary model.', exact=False).wait_for()
            assert len(SyntheticProvider.calls) == call_count, 'Unpriced model triggered inference'
            await panel.get_by_label('Answer model', exact=True).fill('synthetic-ui-model')
            assert await panel.get_by_label('Input price per million tokens (USD)', exact=True).input_value() == '0.1'
            assert await panel.get_by_label('Output price per million tokens (USD)', exact=True).input_value() == '0.2'
            if native_openai:
                assert await panel.get_by_label('Cached input price per million tokens (USD)', exact=True).input_value() == '0.1'
                assert await panel.get_by_label('Cache write price per million tokens (USD)', exact=True).input_value() == '2.5'
            await panel.get_by_label('Fallback answer model', exact=True).fill('synthetic-ui-fallback')
            assert await panel.get_by_label('Fallback input price per million tokens (USD)', exact=True).input_value() == ''
            await panel.get_by_role('button', name='Save and test connection', exact=True).click()
            await panel.get_by_text('Enter separate current input and output token prices for the fallback model.', exact=False).wait_for()
            assert len(SyntheticProvider.calls) == call_count, 'Unpriced fallback triggered inference'
            await panel.get_by_label('Fallback input price per million tokens (USD)', exact=True).fill('0.5')
            await panel.get_by_label('Fallback output price per million tokens (USD)', exact=True).fill('1.1')
            if native_openai:
                await panel.get_by_role('button', name='Save and test connection', exact=True).click()
                await panel.get_by_text('OpenAI also requires nonnegative cached input and cache write rates.', exact=False).wait_for()
                assert len(SyntheticProvider.calls) == call_count, 'Missing fallback cache rates triggered inference'
                await panel.get_by_label('Fallback cached input price per million tokens (USD)', exact=True).fill('0.02')
                await panel.get_by_label('Fallback cache write price per million tokens (USD)', exact=True).fill('0')
            await panel.get_by_role('button', name='Save answer configuration', exact=True).click()
            await expect(panel.get_by_role('button', name='Save answer configuration', exact=True)).to_be_enabled()
            async def saved_prices():
                integration_data = await (await page.request.get(args.backend_url + '/api/integrations', headers=token)).json()
                return next(i['config'] for i in integration_data['items'] if i['provider'] == args.provider)
            exact_prices = (await saved_prices())['answer_model_prices']
            assert exact_prices['synthetic-ui-fallback'] == fallback_rates
            assert exact_prices['synthetic-ui-model'] == primary_rates
            await panel.get_by_label('Fallback answer model', exact=True).fill('synthetic-ui-unpriced-fallback')
            assert await panel.get_by_label('Fallback input price per million tokens (USD)', exact=True).input_value() == ''
            assert await panel.get_by_label('Fallback output price per million tokens (USD)', exact=True).input_value() == ''
            if native_openai:
                assert await panel.get_by_label('Fallback cached input price per million tokens (USD)', exact=True).input_value() == ''
                assert await panel.get_by_label('Fallback cache write price per million tokens (USD)', exact=True).input_value() == ''
            await panel.get_by_label('Fallback answer model', exact=True).fill('synthetic-ui-fallback')
            assert await panel.get_by_label('Fallback input price per million tokens (USD)', exact=True).input_value() == '0.5'
            assert await panel.get_by_label('Fallback output price per million tokens (USD)', exact=True).input_value() == '1.1'
            if native_openai:
                assert await panel.get_by_label('Fallback cached input price per million tokens (USD)', exact=True).input_value() == '0.02'
                assert await panel.get_by_label('Fallback cache write price per million tokens (USD)', exact=True).input_value() == '0'
            await panel.get_by_role('button', name='Save and test connection', exact=True).click()
            await panel.get_by_text('The configured provider returned a response. Review the connection test details below.', exact=True).wait_for()
            await page.screenshot(path=str(out / 'answer-llm-configured.png'), full_page=True)
            await panel.screenshot(path=str(out / 'answer-llm-panel.png'))

            SyntheticProvider.fail = True
            await panel.get_by_role('button', name='Save and test connection', exact=True).click()
            await panel.get_by_text('LLM returned HTTP 401;', exact=False).wait_for()
            assert await panel.get_by_text('The configured provider returned a response.', exact=False).count() == 0
            await page.screenshot(path=str(out / 'answer-llm-failure.png'), full_page=True)

            await page.get_by_role('navigation').get_by_role('button', name='Profile', exact=True).click()
            await page.get_by_role('heading', name='Candidate Intelligence', exact=True).wait_for()
            await page.get_by_label('Exact application question', exact=True).fill('Which high school did you attend?')
            await page.get_by_role('button', name='Explain answer', exact=True).click()
            trial = page.locator('.panel').filter(has=page.get_by_role('heading', name='Try an application question', exact=True)).last
            await trial.get_by_text('No supported answer', exact=True).first.wait_for()
            inspector = trial.locator('.answer-inspector[open]').last
            await inspector.get_by_text('Original Question', exact=True).wait_for()
            assert 'Which high school did you attend?' in await inspector.inner_text()
            assert 'King\'s College London' not in await inspector.inner_text()
            await page.screenshot(path=str(out / 'candidate-intelligence-inspector.png'), full_page=True)
            await trial.screenshot(path=str(out / 'answer-inspector-panel.png'))
            await page.set_viewport_size({'width': 720, 'height': 1000})
            await page.screenshot(path=str(out / 'candidate-intelligence-compact.png'), full_page=True)
            assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth + 2'), 'Compact profile overflows horizontally'
            await browser.close()
        assert not errors, errors
        print(json.dumps({'result': 'passed', 'provider': args.provider + ' via synthetic HTTP fixture; real external LLM not tested',
                          'openai_cache_rates_checked': native_openai,
                          'checked': ['provider/key setup', 'saved-config model discovery', 'model and generation settings persistence', 'exact primary/fallback model prices saved independently', 'model changes clear unsaved rates and restore saved exact rates', 'unpriced primary/fallback rejected before inference', 'legacy provider rates retained without reuse', 'secret not exposed in response/DOM/localStorage', 'synthetic connection prompt privacy', 'failed API connection remains explicit', 'missing secondary education inspector', 'desktop and compact screenshots'], 'screenshots': str(out)}, indent=2))
    finally:
        provider.shutdown()
        provider.server_close()


if __name__ == '__main__':
    asyncio.run(main())

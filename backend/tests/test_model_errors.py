import unittest
from unittest.mock import patch, MagicMock
import httpx
from openai import APIStatusError
from agents.subagents.llm_client import generate, ModelRequestError
from agents.main_agent import classify

class ModelErrors(unittest.TestCase):
 def test_provider_errors_are_not_retried(self):
  for status,body,expected in [(401,{},'rejected OPENAI_API_KEY'),(429,{'code':'insufficient_quota'},'quota or billing'),(429,{},'rate limit')]:
   response=httpx.Response(status,request=httpx.Request('POST','https://api.openai.com/v1/chat/completions'))
   failure=APIStatusError('redacted',response=response,body=body)
   with self.subTest(status=status,body=body), patch.dict('os.environ',{'OPENAI_API_KEY':'test'}), patch('agents.subagents.llm_client.OpenAI') as factory:
    call=factory.return_value.__enter__.return_value.chat.completions.create
    call.side_effect=failure
    with self.assertRaisesRegex(ModelRequestError,expected): generate('system','hi')
    self.assertEqual(call.call_count,1)
 def test_classifier_propagates_provider_error(self):
  with patch('agents.subagents.llm_client.generate',side_effect=ModelRequestError('billing')):
   with self.assertRaisesRegex(ModelRequestError,'billing'): classify('hi')

if __name__=='__main__': unittest.main()

"""Stable capture error categories; never interpolate tool stderr or remote URLs."""

MESSAGES={
    'tls_certificate':'TLS certificate validation failed; the certificate may be untrusted, expired or for another hostname.',
    'tls_handshake':'The TLS handshake could not be completed.',
    'timeout':'The capture timed out because its deadline expired.',
    'connection_failed':'The selected endpoint could not be connected.',
    'connection_refused':'The selected endpoint refused the connection.',
    'connection_reset':'The selected endpoint reset the connection.',
    'dns_resolution':'The selected hostname could not be resolved.',
    'response_truncated':'The service closed before the complete response was received.',
    'response_too_large':'The response exceeded the capture byte limit.',
    'tool_unavailable':'The capture tool is unavailable in this worker image.',
    'tool_failed':'The capture tool could not complete.',
    'capture_failed':'The capture did not complete.',
    'protocol_rejected':'The response did not pass the selected protocol validation.',
}
BROWSER_CODES=frozenset({'navigation_blocked','resource_blocked','http_error','auth_required','network_error',
    'browser_unavailable','capture_expired','browser_operation_failed','controller_timeout','controller_unreachable',
    'invalid_request','session_unavailable','browser_busy'})
CODES=frozenset(MESSAGES)|BROWSER_CODES
CURL_CODES={5:'dns_resolution',6:'dns_resolution',7:'connection_failed',18:'response_truncated',
            28:'timeout',35:'tls_handshake',51:'tls_certificate',58:'tls_certificate',60:'tls_certificate',
            77:'tls_certificate',90:'tls_certificate',91:'tls_certificate',63:'response_too_large'}


class CaptureDiagnosticError(ValueError):
    def __init__(self,code):
        self.code=code if code in MESSAGES else 'capture_failed'
        super().__init__(MESSAGES[self.code])


class CaptureBatchError(ValueError):
    def __init__(self,result):
        self.capture_result=result
        super().__init__(result)
    def __str__(self):
        return 'All fetch actions failed: '+'; '.join(self.capture_result.get('errors',[]))


def error_detail(error,action):
    code=getattr(error,'code','capture_failed')
    if not isinstance(code,str) or code not in CODES:code='capture_failed'
    # Existing parser messages are fixed strings and useful diagnostics; durable
    # worker storage applies the existing integration-secret redaction boundary.
    return {'action':str(action)[:32],'code':code,'message':str(error)[:1000]}

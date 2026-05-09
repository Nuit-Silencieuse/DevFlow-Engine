package com.devflow.engine.service;

import com.devflow.engine.api.LlmCredentialResponse;
import com.devflow.engine.api.LlmCredentialSecretResponse;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

@Service
public class LlmCredentialService {
    private final ConcurrentMap<String, StoredCredential> credentials = new ConcurrentHashMap<>();

    public LlmCredentialResponse createCredential(String provider, String apiKey) {
        if (!StringUtils.hasText(provider)) {
            throw new IllegalArgumentException("LLM credential provider is required.");
        }
        if (!StringUtils.hasText(apiKey)) {
            throw new IllegalArgumentException("LLM credential apiKey is required.");
        }
        String id = "cred_" + UUID.randomUUID();
        StoredCredential credential = new StoredCredential(id, provider.trim(), apiKey.trim());
        credentials.put(id, credential);
        return credential.toResponse();
    }

    public List<LlmCredentialResponse> listCredentials() {
        return credentials.values().stream()
            .map(StoredCredential::toResponse)
            .toList();
    }

    public LlmCredentialSecretResponse getSecret(String credentialId) {
        StoredCredential credential = credentials.get(credentialId);
        if (credential == null) {
            throw new NoSuchElementException("LLM credential not found: " + credentialId);
        }
        return new LlmCredentialSecretResponse(
            credential.id(),
            credential.provider(),
            credential.apiKey()
        );
    }

    private record StoredCredential(String id, String provider, String apiKey) {
        LlmCredentialResponse toResponse() {
            return new LlmCredentialResponse(id, provider, maskApiKey(apiKey));
        }
    }

    static String maskApiKey(String apiKey) {
        if (!StringUtils.hasText(apiKey)) {
            return "";
        }
        String trimmed = apiKey.trim();
        if (trimmed.length() <= 8) {
            return "****";
        }
        return trimmed.substring(0, Math.min(4, trimmed.length())) + "****" + trimmed.substring(trimmed.length() - 4);
    }
}

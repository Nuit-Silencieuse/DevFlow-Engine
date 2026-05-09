package com.devflow.engine.service;

import com.devflow.engine.api.LlmConfigFileResponse;
import com.devflow.engine.api.LlmProviderConfig;
import com.devflow.engine.api.LlmRuntimeConfig;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

@Service
public class LlmConfigFileService {
    private static final TypeReference<LinkedHashMap<String, Object>> JSON_OBJECT_TYPE = new TypeReference<>() {};
    private static final String[] DEFAULT_TIMEOUT_TASKS = {
        "progressive_context_exploration_plan",
        "requirement_analysis",
        "system_design",
        "test_generation",
        "code_review",
        "delivery_integration"
    };
    private static final String[] CODE_GENERATION_TASKS = {
        "code_generation",
        "code_generation_repair"
    };
    private static final Map<String, String[]> STAGE_TASKS = Map.of(
        "REQUIREMENT_ANALYSIS", new String[] {"requirement_analysis"},
        "SYSTEM_DESIGN", new String[] {"system_design"},
        "CODE_GENERATION", CODE_GENERATION_TASKS,
        "TEST_GENERATION", new String[] {"test_generation"},
        "CODE_REVIEW", new String[] {"code_review"},
        "DELIVERY_INTEGRATION", new String[] {"delivery_integration"}
    );

    private final ObjectMapper objectMapper;
    private final Path configPath;

    @Autowired
    public LlmConfigFileService(
        ObjectMapper objectMapper,
        @Value("${devflow.llm.config-file:../../execution-plane/config/llm.local.json}") String configFile
    ) {
        this.objectMapper = objectMapper.copy().enable(SerializationFeature.INDENT_OUTPUT);
        this.configPath = resolveConfigPath(configFile);
    }

    LlmConfigFileService(ObjectMapper objectMapper, Path configPath) {
        this.objectMapper = objectMapper.copy().enable(SerializationFeature.INDENT_OUTPUT);
        this.configPath = configPath.toAbsolutePath().normalize();
    }

    public LlmConfigFileResponse readConfig() {
        return new LlmConfigFileResponse(configPath.toString(), readMutableConfig());
    }

    public LlmConfigFileResponse updateConfig(LlmRuntimeConfig runtimeConfig) {
        LinkedHashMap<String, Object> config = readMutableConfig();
        if (runtimeConfig != null) {
            applyDefaultConfig(config, runtimeConfig.defaultConfig());
            applyStageOverrides(config, runtimeConfig.stageOverrides());
        }
        writeConfig(config);
        return new LlmConfigFileResponse(configPath.toString(), config);
    }

    private void applyDefaultConfig(Map<String, Object> config, LlmProviderConfig providerConfig) {
        if (providerConfig == null) {
            return;
        }
        String provider = providerName(providerConfig.provider(), "openai_compatible");
        putText(config, "defaultProvider", provider);
        putText(config, "defaultModel", providerConfig.model());
        putNumber(config, "timeoutSeconds", providerConfig.timeoutSeconds());
        putNumber(config, "temperature", providerConfig.temperature());
        applyProviderSettings(config, provider, providerConfig);
        applyTaskNumbers(config, "taskTimeoutSeconds", providerConfig.timeoutSeconds(), DEFAULT_TIMEOUT_TASKS);
        applyTaskNumbers(config, "taskMaxTokens", providerConfig.maxTokens(), DEFAULT_TIMEOUT_TASKS);
    }

    private void applyStageOverrides(Map<String, Object> config, Map<String, LlmProviderConfig> stageOverrides) {
        if (stageOverrides == null || stageOverrides.isEmpty()) {
            return;
        }
        stageOverrides.forEach((stageName, stageConfig) -> {
            if (stageName == null || stageConfig == null) {
                return;
            }
            String[] taskNames = STAGE_TASKS.get(stageName.trim().toUpperCase());
            if (taskNames == null) {
                return;
            }
            applyTaskNumbers(config, "taskTimeoutSeconds", stageConfig.timeoutSeconds(), taskNames);
            applyTaskNumbers(config, "taskMaxTokens", stageConfig.maxTokens(), taskNames);
        });
    }

    @SuppressWarnings("unchecked")
    private void applyProviderSettings(Map<String, Object> config, String provider, LlmProviderConfig providerConfig) {
        Map<String, Object> providers = mapChild(config, "providers");
        Map<String, Object> settings = mapChild(providers, provider);
        settings.putIfAbsent("apiKeyEnv", defaultApiKeyEnv(provider));
        putText(settings, "baseUrl", providerConfig.baseUrl());
        putText(settings, "defaultModel", providerConfig.model());
        settings.remove("apiKey");
        settings.remove("credentialId");
    }

    private void applyTaskNumbers(Map<String, Object> config, String key, Number value, String[] tasks) {
        if (value == null || value.doubleValue() <= 0) {
            return;
        }
        Map<String, Object> values = mapChild(config, key);
        for (String task : tasks) {
            values.put(task, value);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> mapChild(Map<String, Object> parent, String key) {
        Object existing = parent.get(key);
        if (existing instanceof Map<?, ?> map) {
            LinkedHashMap<String, Object> normalized = new LinkedHashMap<>();
            map.forEach((itemKey, itemValue) -> normalized.put(String.valueOf(itemKey), itemValue));
            parent.put(key, normalized);
            return normalized;
        }
        LinkedHashMap<String, Object> created = new LinkedHashMap<>();
        parent.put(key, created);
        return created;
    }

    private LinkedHashMap<String, Object> readMutableConfig() {
        if (!Files.exists(configPath)) {
            return new LinkedHashMap<>();
        }
        try {
            return objectMapper.readValue(configPath.toFile(), JSON_OBJECT_TYPE);
        } catch (IOException ex) {
            throw new UncheckedIOException("Failed to read LLM config file: " + configPath, ex);
        }
    }

    private void writeConfig(Map<String, Object> config) {
        try {
            Files.createDirectories(configPath.getParent());
            objectMapper.writeValue(configPath.toFile(), config);
        } catch (IOException ex) {
            throw new UncheckedIOException("Failed to write LLM config file: " + configPath, ex);
        }
    }

    private static void putText(Map<String, Object> target, String key, String value) {
        if (StringUtils.hasText(value)) {
            target.put(key, value.trim());
        }
    }

    private static void putNumber(Map<String, Object> target, String key, Number value) {
        if (value != null && value.doubleValue() >= 0) {
            target.put(key, value);
        }
    }

    private static String providerName(String value, String fallback) {
        return StringUtils.hasText(value) ? value.trim() : fallback;
    }

    private static String defaultApiKeyEnv(String provider) {
        if ("anthropic_compatible".equals(provider)) {
            return "DEVFLOW_LLM_ANTHROPIC_API_KEY";
        }
        return "DEVFLOW_LLM_OPENAI_API_KEY";
    }

    private static Path resolveConfigPath(String configFile) {
        Path configured = Path.of(configFile);
        if (configured.isAbsolute()) {
            return configured.normalize();
        }

        // 控制平面可能从 Maven 模块目录、仓库根目录或 IDE 的自定义目录启动。
        // 先尊重配置值；如果相对路径的父目录不存在，再向上寻找仓库根目录下的执行平面配置。
        Path currentDirectory = Path.of("").toAbsolutePath();
        Path directPath = currentDirectory.resolve(configured).normalize();
        if (directPath.getParent() != null && Files.exists(directPath.getParent())) {
            return directPath;
        }

        Path cursor = currentDirectory;
        while (cursor != null) {
            Path repositoryLocalConfig = cursor.resolve("execution-plane/config/llm.local.json").normalize();
            if (repositoryLocalConfig.getParent() != null && Files.exists(repositoryLocalConfig.getParent())) {
                return repositoryLocalConfig;
            }
            cursor = cursor.getParent();
        }

        return directPath;
    }
}

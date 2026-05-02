package com.devflow.engine.api;

import java.util.List;

public record RepositoryContext(
    String rootPath,
    List<String> includePaths,
    List<String> excludePaths,
    List<String> targetFiles,
    Integer maxFiles,
    Long maxBytes
) {
}

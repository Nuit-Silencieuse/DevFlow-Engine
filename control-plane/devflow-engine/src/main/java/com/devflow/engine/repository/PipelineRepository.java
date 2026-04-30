package com.devflow.engine.repository;

import com.devflow.engine.model.Pipeline;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PipelineRepository extends JpaRepository<Pipeline, UUID> {
}

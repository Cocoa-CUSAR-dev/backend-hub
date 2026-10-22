"""Hubs, hub collectors, processors, stations and batches.

Owns:
    processing.hub
    processing.hub_collector
    processing.processor
    processing.processing_station
    processing.processor_processing_station
    processing.processing_record
    processing.batch
    processing.fermentation_batch
    processing.drying_batch

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here. Empty until the migration moves queries in.
"""

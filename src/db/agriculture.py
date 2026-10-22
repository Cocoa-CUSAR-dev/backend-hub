"""Farmers, farms, plots and every agriculture handler table.

Owns:
    agriculture.farmer
    agriculture.farm
    agriculture.farmer_farm
    agriculture.plot
    agriculture.plot_breed
    agriculture.farm_activity
    agriculture.farm_activity_chemical
    agriculture.farm_activity_fertilizer
    agriculture.farm_pest_disease_record
    agriculture.farm_initial_cost
    agriculture.farm_economic_eval

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here. Empty until the migration moves queries in.
"""

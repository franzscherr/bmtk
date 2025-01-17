import matplotlib.pyplot as plt

from bmtk.analyzer.compartment import plot_traces
from bmtk.analyzer.spike_trains import plot_raster, plot_rates_boxplot


# Setting show to False so we can display all the plots at the same time
plot_raster(config_file='config.json', group_by='model_name', show=False)
plot_rates_boxplot(config_file='config.json', group_by='model_name', show=False)


plot_traces(config_file='config.json', report_name='membrane_potential', group_by='model_name',
            group_excludes=['LIF_exc', 'LIF_inh'], times=(0.0, 200.0), show=False)

plot_traces(config_file='config.json', report_name='calcium_concentration', group_by='model_name',
            group_excludes=['LIF_exc', 'LIF_inh'], times=(0.0, 200.0), show=False)

plt.show()

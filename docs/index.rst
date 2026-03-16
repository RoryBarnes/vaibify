Vaibify Documentation
===========================

``Vaibify`` is a generalized containerization framework for reproducible
scientific computing. It packages code repositories, dependencies, and
pipelines into isolated Docker environments where experiments can be built,
executed, and archived with full provenance.

Vaibify grew out of the
`VVM <https://github.com/RoryBarnes/vvm>`_ (Virtual VPLanet Machine) project
and generalizes its approach so that any scientific workflow -- not just
planetary simulations -- can benefit from containerized reproducibility.

A single ``vaibify init`` command scaffolds a new project, and
``vaibify build`` creates the Docker image. From there, ``start``,
``stop``, ``connect``, ``push``, and ``pull`` manage the running container
while ``publish`` generates GitHub Actions workflows and Zenodo archives.

.. toctree::
   :maxdepth: 2

   quickStart
   setupWizard
   configuration
   pipelines
   reproducibility

.. toctree::
   :maxdepth: 1
   :caption: Links

   GitHub <https://github.com/RoryBarnes/Vaibify>
   PyPI <https://pypi.org/project/vaibify/>

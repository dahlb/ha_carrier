# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                 |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| custom\_components/ha\_carrier/\_\_init\_\_.py                       |      119 |       12 |       18 |        6 |     87% |129, 131-133, 164-165, 198, 239, 242-245, 249, 256, 272-\>277 |
| custom\_components/ha\_carrier/binary\_sensor.py                     |       54 |        2 |        6 |        2 |     93% |37-\>43, 77, 93 |
| custom\_components/ha\_carrier/carrier\_data\_update\_coordinator.py |      157 |       15 |       42 |        6 |     87% |156, 225, 230-231, 272, 309-315, 334-\>340, 344-351, 385-386, 392, 434, 449-450 |
| custom\_components/ha\_carrier/carrier\_entity.py                    |       80 |        9 |       24 |        9 |     83% |70-77, 112, 127-\>126, 129-130, 144-\>143, 146-147, 187, 213-\>215 |
| custom\_components/ha\_carrier/climate.py                            |      226 |       25 |       84 |       18 |     84% |67-\>69, 69-\>71, 71-\>74, 136, 160, 185, 195, 200, 217, 291-\>exit, 302-303, 333-342, 367, 407-\>410, 454, 459-460, 462-463, 466 |
| custom\_components/ha\_carrier/config\_flow.py                       |      125 |        5 |       38 |        9 |     91% |58-\>65, 66-67, 152-\>169, 211, 219, 263-\>292, 278-\>283, 284 |
| custom\_components/ha\_carrier/const.py                              |       31 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/diagnostics.py                        |       32 |        0 |        8 |        2 |     95% |63-\>46, 80-\>87 |
| custom\_components/ha\_carrier/exceptions.py                         |        2 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/migrate.py                            |      234 |       45 |      112 |       27 |     76% |96, 112, 125-129, 177, 181-183, 187, 221, 223-231, 242-247, 251, 270-279, 284-293, 298, 344-\>351, 409-\>412, 412-\>414, 414-\>416, 416-\>419, 433-\>445, 441, 456-\>445, 489, 498-507, 512-521, 586-\>594, 589-590, 595, 663-\>670, 666-667 |
| custom\_components/ha\_carrier/resiliency.py                         |      104 |        7 |       30 |        5 |     91% |167-173, 260, 266, 273, 279, 283 |
| custom\_components/ha\_carrier/select.py                             |       51 |        6 |        8 |        4 |     83% |65, 81, 110, 126, 157-158 |
| custom\_components/ha\_carrier/sensor.py                             |      354 |       60 |      106 |       41 |     78% |76-\>82, 82-\>88, 88-\>94, 117-\>131, 171, 188, 288-290, 295-296, 300-302, 306-\>310, 307-\>306, 311-317, 326-333, 363-364, 368-370, 374-\>378, 375-\>374, 379-385, 420-421, 425-427, 431-\>430, 433-\>430, 437, 468-469, 473-475, 481-\>478, 485, 516-517, 521-523, 529-\>526, 533, 565, 621-622, 650-651, 679-680, 738-739, 793, 802, 804-\>exit, 832-833, 838, 870-871 |
| custom\_components/ha\_carrier/util.py                               |       67 |        5 |       34 |        5 |     90% |94, 98, 131, 134, 142 |
| **TOTAL**                                                            | **1636** |  **191** |  **510** |  **134** | **84%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fdahlb%2Fha_carrier%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.